#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import json
import subprocess
import requests
import sys
import time
from datetime import datetime

def download_latest_file():
    """Télécharge la dernière version du fichier depuis GitHub"""
    url = "https://raw.githubusercontent.com/plague-fetishist/battlebase-data-full/refs/heads/main/battlebase-data.json"
    print(f"Téléchargement du fichier depuis: {url}")
    
    try:
        response = requests.get(url)
        response.raise_for_status()
        
        # Charger le JSON pour modifier uniquement les IDs
        data = json.loads(response.text)
        
        # Liste des apostrophes à remplacer (utilisation des codes Unicode)
        apostrophes_to_replace = [
            "\u0027",  # U+0027 APOSTROPHE
            "\u2019",  # U+2019 RIGHT SINGLE QUOTATION MARK
            "\u2018",  # U+2018 LEFT SINGLE QUOTATION MARK
            "\u201A",  # U+201A SINGLE LOW-9 QUOTATION MARK
            "\u201B",  # U+201B SINGLE HIGH-REVERSED-9 QUOTATION MARK
            "\u00B4",  # U+00B4 ACUTE ACCENT
            "\u0060",  # U+0060 GRAVE ACCENT
            "\u2032",  # U+2032 PRIME
            "\u2035",  # U+2035 REVERSED PRIME
            "\u02B9",  # U+02B9 MODIFIER LETTER PRIME
            "\u02BC",  # U+02BC MODIFIER LETTER APOSTROPHE
        ]
        
        # Remplacer les apostrophes dans les IDs uniquement
        count = 0
        for item in data:
            if 'id' in item:
                original_id = item['id']
                new_id = original_id
                for apostrophe in apostrophes_to_replace:
                    new_id = new_id.replace(apostrophe, "_")
                if new_id != original_id:
                    item['id'] = new_id
                    count += 1
        
        print(f"Remplacement des apostrophes dans {count} IDs")
        
        # Sauvegarder avec le suffixe -en
        with open('battlebase-data-en.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        
        print("Fichier téléchargé avec succès (sauvegardé comme battlebase-data-en.json)")
        return True
    except Exception as e:
        print(f"Erreur lors du téléchargement: {e}")
        return False

def extract_json_from_response(response):
    """Extrait le JSON de la réponse de Claude, même s'il y a du texte avant/après"""
    # Rechercher le premier [ et le dernier ]
    start = response.find('[')
    end = response.rfind(']') + 1
    
    if start != -1 and end > start:
        json_str = response[start:end]
        try:
            # Tenter de parser le JSON
            return json.loads(json_str), json_str
        except json.JSONDecodeError as e:
            # Si ça échoue, essayer de nettoyer le JSON
            # D'abord, essayer de corriger les apostrophes mal échappées
            json_str_fixed = json_str
            
            # Remplacer les apostrophes non échappées dans les valeurs JSON
            # Mais pas dans les IDs (qui sont entre "id": " et ")
            import re
            # Protéger les IDs en les remplaçant temporairement
            id_pattern = r'("id"\s*:\s*"[^"]*")'
            ids = re.findall(id_pattern, json_str_fixed)
            for i, id_match in enumerate(ids):
                json_str_fixed = json_str_fixed.replace(id_match, f"__ID_PLACEHOLDER_{i}__")
            
            # Maintenant échapper les apostrophes dans le reste
            json_str_fixed = json_str_fixed.replace("'", "\\'")
            
            # Restaurer les IDs
            for i, id_match in enumerate(ids):
                json_str_fixed = json_str_fixed.replace(f"__ID_PLACEHOLDER_{i}__", id_match)
            
            try:
                return json.loads(json_str_fixed), json_str_fixed
            except:
                # Si ça échoue toujours, essayer de supprimer les commentaires
                lines = json_str.split('\n')
                cleaned_lines = []
                for line in lines:
                    # Supprimer les commentaires // 
                    comment_pos = line.find('//')
                    if comment_pos != -1:
                        line = line[:comment_pos]
                    cleaned_lines.append(line)
                
                cleaned_json = '\n'.join(cleaned_lines)
                try:
                    return json.loads(cleaned_json), cleaned_json
                except:
                    pass
    
    # Essayer de trouver des objets JSON individuels
    # Parfois Claude retourne {obj1}{obj2} au lieu de [{obj1},{obj2}]
    import re
    objects = re.findall(r'\{[^{}]*\}', response)
    if objects:
        try:
            parsed_objects = [json.loads(obj) for obj in objects]
            return parsed_objects, json.dumps(parsed_objects)
        except:
            pass
    
    return None, None

def preprocess_chunk_for_translation(chunk):
    """Prétraite un chunk pour éviter les problèmes avec les apostrophes"""
    # Créer une copie pour ne pas modifier l'original
    processed_chunk = []
    id_mapping = {}
    
    for i, item in enumerate(chunk):
        item_copy = item.copy()
        original_id = item_copy['id']
        
        # Si l'ID contient des apostrophes (normale ou typographique), créer un ID temporaire
        if "'" in original_id or "'" in original_id:
            id_safe = original_id.replace("'", "_APOS_").replace("'", "_APOS_")
            temp_id = f"TEMP_ID_{i}_{id_safe}"
            item_copy['id'] = temp_id
            id_mapping[temp_id] = original_id
        
        processed_chunk.append(item_copy)
    
    return processed_chunk, id_mapping

def postprocess_translated_chunk(translated_chunk, id_mapping):
    """Restaure les IDs originaux après traduction"""
    if not id_mapping:
        return translated_chunk
    
    processed = []
    for item in translated_chunk:
        item_copy = item.copy()
        if item_copy['id'] in id_mapping:
            item_copy['id'] = id_mapping[item_copy['id']]
        processed.append(item_copy)
    
    return processed

def translate_chunk_with_claude(chunk, chunk_number, max_retries=3):
    """Traduit un chunk avec Claude avec réessais automatiques"""
    print(f"\nTraduction du chunk {chunk_number} ({len(chunk)} entrées)...")
    
    # Plus besoin de prétraitement, les apostrophes ont déjà été remplacées
    processed_chunk = chunk
    id_mapping = {}
    has_apostrophes = False
    
    # Créer le prompt
    prompt = """Tu dois traduire le JSON ci-dessous en français et retourner UNIQUEMENT le JSON traduit.

RÈGLES CRITIQUES:
1. Ta réponse doit commencer DIRECTEMENT par [ sans aucun texte avant
2. Ta réponse doit se terminer par ] sans aucun texte après
3. Ne jamais ajouter d'explication, de commentaire ou de texte en dehors du JSON
4. Garder EXACTEMENT la même structure JSON
5. Ne JAMAIS modifier les clés 'id' - elles doivent rester identiques
6. Traduire les valeurs des clés 'body', 'name', 'description', 'lore', 'whenRules', 'targetRules', 'effectRules', 'restrictionRules' et autres textes
7. Contexte: règles de Warhammer 40000 - utiliser le vocabulaire technique approprié
8. IMPORTANT: Les underscores (_) dans les IDs remplacent des apostrophes et doivent être conservés EXACTEMENT
9. Pour les valeurs null, garder null (pas "null" en string)

EXEMPLE de réponse CORRECTE:
[{"id":"abc123","body":"Texte traduit en français","name":"Nom traduit"}]

EXEMPLE de réponse INCORRECTE:
Voici la traduction: [{"id":"abc123"...}]

JSON à traduire:
"""
    
    chunk_json = json.dumps(processed_chunk, indent=2, ensure_ascii=False)
    full_prompt = prompt + chunk_json
    
    # Debug: afficher le JSON envoyé pour les entrées avec apostrophes
    if has_apostrophes:
        print(f"  [DEBUG] JSON envoyé (après prétraitement):")
        for item in processed_chunk:
            print(f"    ID: {item['id']}")
    
    for attempt in range(max_retries):
        try:
            # Appeler Claude
            if attempt > 0:
                print(f"  Tentative {attempt + 1}/{max_retries}...")
            
            result = subprocess.run(
                ['claude'],
                input=full_prompt,
                capture_output=True,
                text=True,
                encoding='utf-8',
                timeout=120  # Timeout de 2 minutes
            )
            
            if result.returncode != 0:
                print(f"  Erreur Claude (code {result.returncode})")
                print(f"  Stderr: {result.stderr}")
                if result.stdout:
                    print(f"  Stdout: {result.stdout}")
                continue
            
            response = result.stdout.strip()
            
            # Debug: afficher la réponse pour les entrées avec apostrophes
            if has_apostrophes:
                first_line = response.split('\n')[0][:100]
                print(f"  [DEBUG] Réponse de Claude (première ligne): {first_line}...")
                if len(response) < 1000:
                    print(f"  [DEBUG] Réponse complète:\n{response}")
            
            # Extraire le JSON de la réponse avec notre fonction améliorée
            translated_chunk, extracted_json = extract_json_from_response(response)
            
            if translated_chunk:
                # Restaurer les IDs originaux si nécessaire
                if id_mapping:
                    translated_chunk = postprocess_translated_chunk(translated_chunk, id_mapping)
                    print(f"  ✓ IDs originaux restaurés")
                
                print(f"  ✓ Chunk traduit avec succès")
                # Vérifier que nous avons le bon nombre d'entrées
                if len(translated_chunk) != len(chunk):
                    print(f"  ⚠️  Attention: {len(chunk)} entrées envoyées, {len(translated_chunk)} reçues")
                    # Afficher les IDs manquants
                    sent_ids = {item['id'] for item in chunk}
                    received_ids = {item['id'] for item in translated_chunk}
                    missing_ids = sent_ids - received_ids
                    if missing_ids:
                        print(f"  IDs manquants dans la réponse: {missing_ids}")
                    if attempt < max_retries - 1:
                        print(f"  Réessai...")
                        continue
                return translated_chunk
            else:
                if attempt < max_retries - 1:
                    print(f"  ✗ Impossible d'extraire du JSON valide, réessai...")
                    # Afficher la réponse complète pour debug
                    print(f"  Réponse de Claude:")
                    print("-" * 60)
                    print(response[:500] + "..." if len(response) > 500 else response)
                    print("-" * 60)
                else:
                    print(f"  ✗ Impossible d'extraire du JSON après {max_retries} tentatives")
                    if "Execution error" in response or "error" in response.lower():
                        print(f"  Claude a renvoyé une erreur. Réduction de la taille du chunk.")
                        return None  # Retourner None pour que le script continue avec une taille plus petite
                    else:
                        print(f"  Réponse complète de Claude:")
                        print("-" * 60)
                        print(response)
                        print("-" * 60)
                        # Sauvegarder la réponse problématique pour debug
                        debug_file = f"debug_chunk_{chunk_number}_attempt_{attempt}.txt"
                        with open(debug_file, 'w', encoding='utf-8') as f:
                            f.write(f"Prompt:\n{full_prompt}\n\n")
                            f.write(f"Réponse:\n{response}")
                        print(f"  Réponse sauvegardée dans {debug_file}")
                        print(f"\nArrêt du script.")
                        sys.exit(1)
                    
        except subprocess.TimeoutExpired:
            print(f"  ✗ Timeout: Claude n'a pas répondu après 2 minutes")
            raise  # On relance l'exception pour la gérer plus haut
        except Exception as e:
            print(f"  ✗ Erreur: {type(e).__name__}: {e}")
            if attempt < max_retries - 1:
                print(f"  Réessai...")
            else:
                print(f"  ✗ Échec après {max_retries} tentatives")
                # Debug: afficher les IDs du chunk pour voir s'il y a des apostrophes
                ids_in_chunk = [item.get('id', 'NO_ID') for item in chunk]
                if any("'" in id for id in ids_in_chunk):
                    print(f"  ⚠️  Ce chunk contient des IDs avec apostrophes:")
                    for id in ids_in_chunk:
                        if "'" in id:
                            print(f"     - {id}")
                return None
    
    return None

def push_to_github():
    """Crée une nouvelle branche et pousse le fichier traduit"""
    date_str = datetime.now().strftime('%Y_%m_%d')
    branch_name = f"new_translation_{date_str}"
    
    print(f"\n{'='*60}")
    print(f"Push vers GitHub sur la branche: {branch_name}")
    print(f"{'='*60}")
    
    try:
        # Créer et checkout la nouvelle branche
        print("Création de la nouvelle branche...")
        subprocess.run(['git', 'checkout', '-b', branch_name], check=True)
        
        # Ajouter le fichier traduit
        print("Ajout du fichier traduit...")
        subprocess.run(['git', 'add', 'battlebase-data.json'], check=True)
        
        # Créer le commit
        print("Création du commit...")
        commit_message = f"Traduction automatique du {date_str}"
        subprocess.run(['git', 'commit', '-m', commit_message], check=True)
        
        # Pousser la branche
        print("Push de la branche vers GitHub...")
        subprocess.run(['git', 'push', '-u', 'origin', branch_name], check=True)
        
        print(f"\n✅ Branche '{branch_name}' créée et poussée avec succès!")
        print(f"   Vous pouvez créer une pull request à l'adresse:")
        print(f"   https://github.com/supadfr/battlebase-data-fr-auto/compare/{branch_name}")
        
        # Retour sur main
        subprocess.run(['git', 'checkout', 'main'], check=True)
        
        return True
        
    except subprocess.CalledProcessError as e:
        print(f"\n❌ Erreur lors du push: {e}")
        # Essayer de revenir sur main en cas d'erreur
        try:
            subprocess.run(['git', 'checkout', 'main'], check=True)
        except:
            pass
        return False

def main():
    # Télécharger le fichier
    if not download_latest_file():
        return
    
    # Charger le fichier original
    print("\nChargement du fichier...")
    with open('battlebase-data-en.json', 'r', encoding='utf-8') as f:
        data = json.load(f)
    
    print(f"Total: {len(data)} entrées")
    
    # Initialiser
    translated_data = []
    output_file = 'battlebase-data.json'
    
    # Variables pour la logique adaptative
    current_chunk_size = 18  # On commence avec 18 entrées
    optimal_chunk_size = None  # Taille optimale trouvée
    position = 0
    chunk_number = 0
    
    # Dictionnaire pour tracker les entrées traduites par ID
    translated_ids = set()
    
    # Traiter les entrées
    while position < len(data):
        chunk_number += 1
        
        # Si on a trouvé la taille optimale et qu'elle n'a pas timeout récemment, l'utiliser
        if optimal_chunk_size is not None and current_chunk_size >= optimal_chunk_size:
            current_chunk_size = optimal_chunk_size
            
            # Calculer les chunks restants
            entries_remaining = len(data) - position
            chunks_remaining = (entries_remaining + current_chunk_size - 1) // current_chunk_size
            estimated_time_seconds = chunks_remaining * 120  # 1 chunk ~= 2 minutes
            
            # Formatage en hh:mm:ss
            hours = estimated_time_seconds // 3600
            minutes = (estimated_time_seconds % 3600) // 60
            seconds = estimated_time_seconds % 60
            
            print(f"\n{'='*60}")
            print(f"Utilisation de la taille optimale: {current_chunk_size} entrées/chunk")
            print(f"Chunks restants: {chunks_remaining}")
            print(f"Temps estimé: ~{hours:02d}:{minutes:02d}:{seconds:02d}")
            print(f"{'='*60}")
        else:
            print(f"\nTest avec {current_chunk_size} entrées/chunk")
        
        # Extraire le chunk
        chunk = data[position:position + current_chunk_size]
        
        try:
            # Traduire le chunk
            translated_chunk = translate_chunk_with_claude(chunk, chunk_number)
            
            if translated_chunk:
                # Ajouter uniquement les entrées non déjà traduites
                for item in translated_chunk:
                    if item['id'] not in translated_ids:
                        translated_data.append(item)
                        translated_ids.add(item['id'])
                
                position += len(chunk)
                
                # Mettre à jour la taille optimale si on a trouvé mieux
                if optimal_chunk_size is None or current_chunk_size < optimal_chunk_size:
                    optimal_chunk_size = current_chunk_size
                    print(f"  ✅ Taille optimale {'confirmée' if optimal_chunk_size == current_chunk_size else 'mise à jour'}: {optimal_chunk_size} entrées/chunk")
                
                # Sauvegarder après chaque chunk
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(translated_data, f, indent=2, ensure_ascii=False)
                
                print(f"  Progression: {len(translated_data)}/{len(data)} entrées traduites")
                
                # Petite pause entre les chunks
                if position < len(data):
                    time.sleep(1)
            else:
                # Échec de traduction (autre que timeout)
                print(f"  ⚠️  Échec de la traduction du chunk {chunk_number}")
                # Réduire la taille et réessayer
                if current_chunk_size > 1:
                    current_chunk_size -= 1
                    print(f"  Réduction de la taille à {current_chunk_size}")
                else:
                    # Si même avec 1 entrée ça échoue, on passe
                    print(f"  Impossible de traduire cette entrée, passage au suivant")
                    position += 1
                    
        except subprocess.TimeoutExpired:
            # Timeout atteint
            print(f"  ⚠️  Timeout avec {current_chunk_size} entrées")
            if current_chunk_size > 1:
                # Réduire la taille de 1
                current_chunk_size -= 1
                print(f"  Réduction de la taille à {current_chunk_size}")
                # Si la taille optimale était celle qui a timeout, la mettre à jour
                if optimal_chunk_size and optimal_chunk_size > current_chunk_size:
                    optimal_chunk_size = current_chunk_size
                    print(f"  Mise à jour de la taille optimale à {optimal_chunk_size}")
                # On ne change pas la position, on réessaye le même chunk avec moins d'entrées
            else:
                # Si timeout même avec 1 entrée, on passe
                print(f"  Timeout même avec 1 entrée, passage au suivant")
                position += 1
    
    # Vérification finale et traitement des entrées manquantes
    print(f"\n{'='*60}")
    print(f"Traduction terminée!")
    print(f"  Entrées originales: {len(data)}")
    print(f"  Entrées traduites: {len(translated_data)}")
    
    if optimal_chunk_size:
        print(f"  Taille optimale utilisée: {optimal_chunk_size} entrées par chunk")
    
    # Vérifier s'il manque des entrées - boucle jusqu'à ce que tout soit traduit ou qu'on ne progresse plus
    max_retry_rounds = 3
    retry_round = 0
    
    while len(translated_data) < len(data) and retry_round < max_retry_rounds:
        retry_round += 1
        missing = len(data) - len(translated_data)
        print(f"\n{'='*60}")
        print(f"Round de rattrapage {retry_round}/{max_retry_rounds}")
        print(f"  ⚠️  {missing} entrées manquantes")
        
        # Identifier précisément les entrées manquantes
        missing_entries = [item for item in data if item['id'] not in translated_ids]
        
        if missing_entries:
            print(f"\nPhase de rattrapage pour {len(missing_entries)} entrées...")
            
            # Afficher les IDs manquants pour debug
            print("\nEntrées manquantes:")
            apostrophe_entries = []
            for entry in missing_entries[:10]:  # Afficher max 10 pour ne pas encombrer
                if "'" in entry['id']:
                    print(f"  - {entry['id']} (contient apostrophe)")
                    apostrophe_entries.append(entry)
                else:
                    print(f"  - {entry['id']}")
            if len(missing_entries) > 10:
                print(f"  ... et {len(missing_entries) - 10} autres")
            
            # Traiter d'abord les entrées avec apostrophes individuellement
            if apostrophe_entries:
                print(f"\nTraitement spécial pour {len(apostrophe_entries)} entrées avec apostrophes...")
                for entry in apostrophe_entries:
                    chunk_number += 1
                    print(f"\nTraduction individuelle de: {entry['id']}")
                    translated_single = translate_chunk_with_claude([entry], chunk_number, max_retries=5)
                    if translated_single:
                        added = False
                        for item in translated_single:
                            if item['id'] not in translated_ids and item['id'] == entry['id']:
                                translated_data.append(item)
                                translated_ids.add(item['id'])
                                missing_entries.remove(entry)
                                added = True
                        # Sauvegarder
                        with open(output_file, 'w', encoding='utf-8') as f:
                            json.dump(translated_data, f, indent=2, ensure_ascii=False)
                        if added:
                            print(f"  ✓ Traduit avec succès")
                        else:
                            print(f"  ⚠️  Traduit mais ID non correspondant")
                    else:
                        print(f"  ✗ Échec de la traduction")
                        # Essayer une approche manuelle en dernier recours
                        print(f"  Tentative de traduction manuelle...")
                        manual_prompt = f"""Traduis ce stratagème Warhammer 40000 en français. Retourne UNIQUEMENT un objet JSON valide.

{json.dumps(entry, indent=2, ensure_ascii=False)}

RÈGLES:
- Garde l'ID exactement comme il est (avec l'apostrophe)
- Traduis "lore", "whenRules", "targetRules", "effectRules", "restrictionRules"
- Pour null, garde null

Retourne UNIQUEMENT le JSON traduit, sans texte avant ou après."""
                        
                        try:
                            result = subprocess.run(
                                ['claude'],
                                input=manual_prompt,
                                capture_output=True,
                                text=True,
                                encoding='utf-8',
                                timeout=60
                            )
                            
                            if result.returncode == 0:
                                response = result.stdout.strip()
                                # Essayer d'extraire un objet JSON unique
                                import re
                                json_match = re.search(r'\{[^{}]*\}', response, re.DOTALL)
                                if json_match:
                                    try:
                                        translated_obj = json.loads(json_match.group())
                                        if translated_obj['id'] == entry['id']:
                                            translated_data.append(translated_obj)
                                            translated_ids.add(translated_obj['id'])
                                            missing_entries.remove(entry)
                                            with open(output_file, 'w', encoding='utf-8') as f:
                                                json.dump(translated_data, f, indent=2, ensure_ascii=False)
                                            print(f"    ✓ Traduction manuelle réussie!")
                                    except:
                                        print(f"    ✗ Échec du parsing JSON manuel")
                        except:
                            print(f"    ✗ Échec de la traduction manuelle")
            
            # Utiliser une taille de chunk sûre pour le rattrapage du reste
            safe_chunk_size = min(6, optimal_chunk_size or 6)
            retry_position = 0
            
            while retry_position < len(missing_entries):
                chunk_number += 1
                # Prendre un chunk d'entrées manquantes
                retry_chunk = missing_entries[retry_position:retry_position + safe_chunk_size]
                
                print(f"\nTraduction du chunk de rattrapage ({len(retry_chunk)} entrées)...")
                
                # Essayer de traduire avec plus de tentatives
                translated_retry = translate_chunk_with_claude(retry_chunk, chunk_number, max_retries=5)
                
                if translated_retry:
                    # Ajouter les entrées traduites
                    for item in translated_retry:
                        if item['id'] not in translated_ids:
                            translated_data.append(item)
                            translated_ids.add(item['id'])
                    
                    # Sauvegarder
                    with open(output_file, 'w', encoding='utf-8') as f:
                        json.dump(translated_data, f, indent=2, ensure_ascii=False)
                    
                    print(f"  ✓ Rattrapage réussi - Progression: {len(translated_data)}/{len(data)}")
                    retry_position += len(retry_chunk)
                else:
                    # Si échec, réduire la taille ou passer à l'entrée suivante
                    if safe_chunk_size > 1:
                        safe_chunk_size = max(1, safe_chunk_size // 2)
                        print(f"  Réduction de la taille de rattrapage à {safe_chunk_size}")
                    else:
                        print(f"  ❌ Impossible de traduire cette entrée, passage au suivant")
                        retry_position += 1
            
            print(f"\nAprès rattrapage:")
            print(f"  Entrées traduites: {len(translated_data)}/{len(data)}")
        
        # Si on n'a fait aucun progrès, arrêter
        if len(missing_entries) == missing:
            print(f"\n⚠️  Aucun progrès dans ce round de rattrapage")
            break
    
    # Remplacer les _ par des - dans tous les IDs
    print("\nRemplacement des _ par des - dans les IDs...")
    for item in translated_data:
        if 'id' in item:
            item['id'] = item['id'].replace('_', '-')
    
    # Sauvegarder avec les IDs modifiés
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(translated_data, f, indent=2, ensure_ascii=False)
    
    # Vérification finale en tenant compte des possibles doublons
    print("\n" + "="*60)
    print("Vérification finale...")
    
    # Charger le fichier de sortie pour vérifier tous les IDs présents
    with open(output_file, 'r', encoding='utf-8') as f:
        output_data = json.load(f)
    
    # Créer un ensemble de tous les IDs dans le fichier de sortie (avec normalisation)
    output_ids_raw = {item['id'] for item in output_data}
    output_ids_normalized = {id.replace("-", "_").replace("'", "_") for id in output_ids_raw}
    
    # Créer un ensemble des IDs originaux normalisés
    original_ids_normalized = {item['id'].replace("-", "_").replace("'", "_") for item in data}
    
    # Vérifier quels IDs originaux ne sont pas dans le fichier de sortie
    missing_ids_normalized = original_ids_normalized - output_ids_normalized
    
    # Retrouver les IDs originaux correspondants
    truly_missing_entries = []
    for item in data:
        normalized_id = item['id'].replace("-", "_").replace("'", "_")
        if normalized_id in missing_ids_normalized:
            truly_missing_entries.append(item)
    
    print(f"  Entrées dans le fichier original: {len(data)}")
    print(f"  Entrées dans le fichier de sortie: {len(output_data)}")
    print(f"  Entrées réellement manquantes: {len(truly_missing_entries)}")
    
    # Si des doublons ont été détectés
    if len(output_data) > len(translated_data):
        print(f"  ⚠️  Doublons détectés: {len(output_data) - len(translated_data)} entrées en double")
    
    # Résultat final
    if len(truly_missing_entries) == 0:
        print("\n✅ Toutes les entrées ont été traduites avec succès!")
        push_to_github()
    else:
        print(f"\n⚠️  {len(truly_missing_entries)} entrées n'ont pas pu être traduites après {retry_round} rounds de rattrapage")
        print("⚠️  Push annulé: la traduction n'est pas complète")
        
        # Sauvegarder les IDs des entrées réellement non traduites
        untranslated_ids = [item['id'] for item in truly_missing_entries]
        with open('untranslated_ids.txt', 'w', encoding='utf-8') as f:
            if untranslated_ids:
                f.write('\n'.join(untranslated_ids))
                print(f"   {len(untranslated_ids)} IDs non traduits ont été sauvegardés dans untranslated_ids.txt")
                # Afficher les premiers IDs manquants
                print("\n   Exemples d'IDs manquants:")
                for id in untranslated_ids[:5]:
                    print(f"     - {id}")
                if len(untranslated_ids) > 5:
                    print(f"     ... et {len(untranslated_ids) - 5} autres")
            else:
                f.write("Aucun ID non traduit trouvé")
                print("   ✅ Aucun ID réellement manquant")

if __name__ == "__main__":
    main()