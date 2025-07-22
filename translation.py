#!/usr/bin/env python3
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
        
        # Sauvegarder avec le suffixe -en
        with open('battlebase-data-en.json', 'w', encoding='utf-8') as f:
            f.write(response.text)
        
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
        except json.JSONDecodeError:
            # Si ça échoue, essayer de nettoyer le JSON
            # Supprimer les commentaires éventuels
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

def translate_chunk_with_claude(chunk, chunk_number, max_retries=3):
    """Traduit un chunk avec Claude avec réessais automatiques"""
    print(f"\nTraduction du chunk {chunk_number} ({len(chunk)} entrées)...")
    
    # Créer le prompt
    prompt = """Tu dois traduire le JSON ci-dessous en français et retourner UNIQUEMENT le JSON traduit.

RÈGLES CRITIQUES:
1. Ta réponse doit commencer DIRECTEMENT par [ sans aucun texte avant
2. Ta réponse doit se terminer par ] sans aucun texte après
3. Ne jamais ajouter d'explication, de commentaire ou de texte en dehors du JSON
4. Garder EXACTEMENT la même structure JSON
5. Ne JAMAIS modifier les clés 'id' - elles doivent rester identiques
6. Traduire les valeurs des clés 'body', 'name', 'description' et autres textes
7. Contexte: règles de Warhammer 40000 - utiliser le vocabulaire technique approprié

EXEMPLE de réponse CORRECTE:
[{"id":"abc123","body":"Texte traduit en français","name":"Nom traduit"}]

EXEMPLE de réponse INCORRECTE:
Voici la traduction: [{"id":"abc123"...}]

JSON à traduire:
"""
    
    chunk_json = json.dumps(chunk, indent=2, ensure_ascii=False)
    full_prompt = prompt + chunk_json
    
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
                if attempt == max_retries - 1:
                    print(f"  Stderr: {result.stderr}")
                continue
            
            response = result.stdout.strip()
            
            # Extraire le JSON de la réponse avec notre fonction améliorée
            translated_chunk, extracted_json = extract_json_from_response(response)
            
            if translated_chunk:
                print(f"  ✓ Chunk traduit avec succès")
                # Vérifier que nous avons le bon nombre d'entrées
                if len(translated_chunk) != len(chunk):
                    print(f"  ⚠️  Attention: {len(chunk)} entrées envoyées, {len(translated_chunk)} reçues")
                    if attempt < max_retries - 1:
                        print(f"  Réessai...")
                        continue
                return translated_chunk
            else:
                if attempt < max_retries - 1:
                    print(f"  ✗ Impossible d'extraire du JSON valide, réessai...")
                    # Afficher un aperçu de la réponse pour debug
                    preview = response[:200] + "..." if len(response) > 200 else response
                    print(f"  Aperçu de la réponse: {preview}")
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
            if attempt < max_retries - 1:
                print(f"  ✗ Erreur: {e}, réessai...")
            else:
                print(f"  ✗ Erreur après {max_retries} tentatives: {e}")
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
    current_chunk_size = 6  # On commence avec 6 entrées
    max_chunk_size_reached = None  # Taille max avant timeout
    optimal_size_found = False
    last_successful_size = 0  # Dernière taille qui a fonctionné (sera mise à jour après le premier succès)
    position = 0
    chunk_number = 0
    
    # Traiter les entrées
    while position < len(data):
        chunk_number += 1
        
        # Si on a trouvé la taille optimale
        if optimal_size_found:
            current_chunk_size = last_successful_size
            
            # Calculer les chunks restants
            entries_remaining = len(data) - position
            chunks_remaining = (entries_remaining + current_chunk_size - 1) // current_chunk_size
            estimated_time_minutes = chunks_remaining * 2  # 1 chunk ~= 2 minutes avec le nouveau timeout
            estimated_time_hours = estimated_time_minutes / 60
            
            print(f"\n{'='*60}")
            print(f"Utilisation de la taille optimale: {current_chunk_size} entrées/chunk")
            print(f"Chunks restants: {chunks_remaining}")
            if estimated_time_hours >= 1:
                print(f"Temps estimé: ~{estimated_time_hours:.1f} heures")
            else:
                print(f"Temps estimé: ~{estimated_time_minutes} minutes")
            print(f"{'='*60}")
        else:
            # Phase de recherche
            if max_chunk_size_reached is None:
                # On double à chaque fois tant qu'on n'a pas touché de timeout
                if chunk_number == 1:
                    current_chunk_size = 6  # On commence avec 6 entrées
                else:
                    current_chunk_size = last_successful_size * 2
                print(f"\n{current_chunk_size} entrées")
            else:
                # On a touché un timeout, on fait une recherche dichotomique
                # entre last_successful_size et max_chunk_size_reached
                current_chunk_size = last_successful_size + (max_chunk_size_reached - last_successful_size) // 2
                
                # Si la différence est <= 1, on a trouvé la taille optimale
                if max_chunk_size_reached - last_successful_size <= 1:
                    optimal_size_found = True
                    current_chunk_size = last_successful_size
                    print(f"\n✅ Taille optimale trouvée: {current_chunk_size} entrées/chunk")
                else:
                    print(f"\nRecherche dichotomique: test avec {current_chunk_size} entrées")
                    print(f"  (entre {last_successful_size} qui fonctionne et {max_chunk_size_reached} qui timeout)")
        
        # Extraire le chunk
        chunk = data[position:position + current_chunk_size]
        
        try:
            # Traduire le chunk
            translated_chunk = translate_chunk_with_claude(chunk, chunk_number)
            
            if translated_chunk:
                translated_data.extend(translated_chunk)
                position += len(chunk)
                
                # Mise à jour de la dernière taille qui a fonctionné
                if not optimal_size_found:
                    last_successful_size = current_chunk_size
                
                # Sauvegarder après chaque chunk
                with open(output_file, 'w', encoding='utf-8') as f:
                    json.dump(translated_data, f, indent=2, ensure_ascii=False)
                
                print(f"  Progression: {len(translated_data)}/{len(data)} entrées traduites")
                
                # Si on a trouvé la taille optimale, afficher le temps restant
                if optimal_size_found and position < len(data):
                    entries_remaining = len(data) - position
                    chunks_remaining = (entries_remaining + current_chunk_size - 1) // current_chunk_size
                    estimated_minutes = chunks_remaining * 2
                    if estimated_minutes >= 60:
                        estimated_hours = estimated_minutes / 60
                        print(f"  Chunks restants: {chunks_remaining} (~{estimated_hours:.1f} heures)")
                    else:
                        print(f"  Chunks restants: {chunks_remaining} (~{estimated_minutes} minutes)")
                
                # Petite pause
                if position < len(data):
                    time.sleep(1)
            else:
                print(f"  ⚠️  Échec de la traduction du chunk {chunk_number}")
                # En phase de recherche, on ajuste max_chunk_size_reached
                if not optimal_size_found:
                    # Si même avec une petite taille ça échoue, on essaie de continuer
                    if current_chunk_size <= 2:
                        print(f"  Erreur même avec {current_chunk_size} entrées. Passage au chunk suivant.")
                        position += current_chunk_size
                    else:
                        max_chunk_size_reached = current_chunk_size
                        chunk_number -= 1  # On refait ce chunk
                else:
                    # Si on a déjà trouvé la taille optimale et que ça échoue quand même
                    print(f"  Erreur inattendue avec la taille optimale")
                    position += current_chunk_size  # On saute ce chunk
                    
        except subprocess.TimeoutExpired:
            # Timeout atteint
            print(f"  ⚠️  Timeout atteint avec {current_chunk_size} entrées")
            if not optimal_size_found:
                max_chunk_size_reached = current_chunk_size
                chunk_number -= 1  # On refait ce chunk avec une taille plus petite
            else:
                print(f"  Erreur: timeout avec la taille optimale {current_chunk_size}")
                position += current_chunk_size  # On saute ce chunk
    
    # Vérification finale
    print(f"\n{'='*60}")
    print(f"Traduction terminée!")
    print(f"  Entrées originales: {len(data)}")
    print(f"  Entrées traduites: {len(translated_data)}")
    
    if max_chunk_size_reached:
        print(f"  Taille optimale trouvée: {max_chunk_size_reached - 1} entrées par chunk")
    
    if len(translated_data) == len(data):
        print("  ✅ Toutes les entrées ont été traduites!")
    else:
        missing = len(data) - len(translated_data)
        print(f"  ⚠️  {missing} entrées manquantes")
    
    # Push vers GitHub si la traduction est complète
    if len(translated_data) == len(data):
        push_to_github()
    else:
        print("\n⚠️  Push annulé: la traduction n'est pas complète")

if __name__ == "__main__":
    main()