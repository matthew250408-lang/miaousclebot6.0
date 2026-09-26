"""
Auto Changelog System - Détecte les changements de commandes
"""
import os
import json
import hashlib
from typing import Dict, List, Tuple, Optional
import discord

HASHES_FILE = "file_hashes.json"
BACKUPS_FILE = "file_backups.json"
VERSION_FILE = "version.json"

def get_file_hash(filepath: str) -> str:
    """Calcule le hash SHA256 d'un fichier"""
    sha256_hash = hashlib.sha256()
    try:
        with open(filepath, "rb") as f:
            for byte_block in iter(lambda: f.read(4096), b""):
                sha256_hash.update(byte_block)
        return sha256_hash.hexdigest()
    except:
        return ""

def get_all_file_hashes() -> Dict[str, str]:
    """Récupère les hashes de tous les fichiers à tracker"""
    hashes = {}
    
    files_to_track = ["main.py"]
    dirs_to_track = ["cogs/", "utils/", "translations/"]
    
    for file in files_to_track:
        if os.path.exists(file):
            hashes[file] = get_file_hash(file)
    
    for directory in dirs_to_track:
        if os.path.isdir(directory):
            for root, dirs, files in os.walk(directory):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for file in sorted(files):
                    if file.endswith('.py') or file.endswith('.json'):
                        filepath = os.path.join(root, file)
                        hashes[filepath] = get_file_hash(filepath)
    
    return hashes

def get_file_content(filepath: str) -> str:
    """Récupère le contenu complet d'un fichier"""
    try:
        with open(filepath, "r", encoding="utf-8", errors="ignore") as f:
            return f.read()
    except:
        return ""

def get_all_file_contents() -> Dict[str, str]:
    """Récupère le contenu de tous les fichiers à tracker"""
    contents = {}
    
    files_to_track = ["main.py"]
    dirs_to_track = ["cogs/", "utils/", "translations/"]
    
    for file in files_to_track:
        if os.path.exists(file):
            contents[file] = get_file_content(file)
    
    for directory in dirs_to_track:
        if os.path.isdir(directory):
            for root, dirs, files in os.walk(directory):
                dirs[:] = [d for d in dirs if not d.startswith('.')]
                for file in sorted(files):
                    if file.endswith('.py') or file.endswith('.json'):
                        filepath = os.path.join(root, file)
                        contents[filepath] = get_file_content(filepath)
    
    return contents

def load_last_hashes() -> Dict[str, str]:
    """Charge les hashes du dernier démarrage"""
    try:
        with open(HASHES_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def load_backups() -> Dict[str, str]:
    """Charge le contenu sauvegardé"""
    try:
        with open(BACKUPS_FILE, "r") as f:
            return json.load(f)
    except:
        return {}

def save_hashes(hashes: Dict[str, str]) -> None:
    """Sauvegarde les hashes actuels"""
    with open(HASHES_FILE, "w") as f:
        json.dump(hashes, f, indent=2)

def save_backups(contents: Dict[str, str]) -> None:
    """Sauvegarde le contenu actuel"""
    with open(BACKUPS_FILE, "w") as f:
        json.dump(contents, f, indent=2)

def get_current_version() -> str:
    """Récupère la version actuelle"""
    try:
        with open(VERSION_FILE, "r") as f:
            data = json.load(f)
            return data.get("version", "v11.43.0")
    except:
        return "v11.43.0"

def increment_version(current_version: str) -> str:
    """Incrémente la version"""
    try:
        parts = current_version.replace("v", "").split(".")
        if len(parts) >= 3:
            parts[-1] = str(int(parts[-1]) + 1)
            return "v" + ".".join(parts)
    except:
        pass
    return current_version

def save_version(version: str) -> None:
    """Sauvegarde la version"""
    with open(VERSION_FILE, "w") as f:
        json.dump({"version": version}, f, indent=2)

def extract_commands(content: str) -> set:
    """Extrait les noms des commandes slash du contenu"""
    commands = set()
    lines = content.split("\n")
    for i, line in enumerate(lines):
        if "@app_commands.command" in line:
            for next_line in lines[i:min(i+5, len(lines))]:
                if "async def " in next_line or "def " in next_line:
                    try:
                        func_name = next_line.split("def ")[1].split("(")[0].strip()
                        if func_name:
                            commands.add(func_name)
                    except:
                        pass
                    break
    return commands

def extract_functions_with_docs(content: str) -> dict:
    """Extrait toutes les fonctions avec leurs docstrings"""
    functions = {}
    lines = content.split("\n")
    
    for i, line in enumerate(lines):
        # Chercher les définitions de fonctions
        if line.strip().startswith("def ") or line.strip().startswith("async def "):
            try:
                func_name = line.split("def ")[1].split("(")[0].strip()
                
                # Chercher la docstring
                docstring = ""
                for j in range(i+1, min(i+10, len(lines))):
                    if '"""' in lines[j] or "'''" in lines[j]:
                        # Début d'une docstring
                        start_quote = '"""' if '"""' in lines[j] else "'''"
                        first_line = lines[j].split(start_quote)[1] if start_quote in lines[j] else ""
                        
                        if first_line and start_quote in lines[j].split(start_quote)[2:]:
                            # Une ligne
                            docstring = first_line
                        else:
                            # Multi-lignes
                            for k in range(j+1, min(j+5, len(lines))):
                                if start_quote in lines[k]:
                                    docstring = lines[j] + " " + " ".join(lines[j+1:k])
                                    break
                        break
                
                if func_name and func_name != "_":
                    functions[func_name] = docstring.strip().replace('"""', '').replace("'''", '')
            except:
                pass
    
    return functions

def detect_command_changes(old_content: str, new_content: str) -> Tuple[List[str], List[str]]:
    """
    Détecte les changements de commandes
    Retourne (added, removed)
    """
    added = []
    removed = []
    
    if not old_content or not new_content:
        return added, removed
    
    old_commands = extract_commands(old_content)
    new_commands = extract_commands(new_content)
    
    # Commandes supprimées
    for cmd in sorted(old_commands - new_commands):
        removed.append(cmd)
    
    # Commandes nouvelles
    for cmd in sorted(new_commands - old_commands):
        added.append(cmd)
    
    return added, removed

def detect_improvements(old_content: str, new_content: str, filepath: str) -> List[str]:
    """
    Détecte les améliorations et changements de code
    Retourne une liste des améliorations détectées
    """
    improvements = []
    
    if not old_content or not new_content:
        return improvements
    
    old_funcs = extract_functions_with_docs(old_content)
    new_funcs = extract_functions_with_docs(new_content)
    
    # Détections intelligentes basées sur le nom du fichier et les changements
    if "moderation" in filepath.lower():
        if "generate_sanction_id" in new_funcs and "generate_sanction_id" not in old_funcs:
            improvements.append("✨ Nouveau système d'ID intelligent pour les sanctions")
        elif "generate_sanction_id" in new_funcs and "generate_sanction_id" in old_funcs:
            if len(new_funcs["generate_sanction_id"]) > len(old_funcs.get("generate_sanction_id", "")):
                improvements.append("⚙️ Amélioration du système d'ID pour les sanctions")
    
    if "levels" in filepath.lower():
        if "get_user_level" in new_funcs and new_funcs.get("get_user_level"):
            improvements.append("📊 Système de niveaux amélioré")
    
    if "general" in filepath.lower():
        if "ping" in new_funcs and "ping" in old_funcs:
            improvements.append("🏓 Amélioration de la commande /ping")
    
    # Détection générique des changements importants
    new_only = set(new_funcs.keys()) - set(old_funcs.keys())
    for func in new_only:
        if not func.startswith("_"):  # Ignorer les fonctions privées
            if "improvement" in new_funcs[func].lower():
                improvements.append(f"✨ Nouvelle fonction: {func}")
    
    return improvements

# REMOVED - recap system deleted

def create_log_embed(added_cmds: List[str], removed_cmds: List[str], version: str, improvements: Optional[List[str]] = None) -> discord.Embed:
    """Crée un embed LOG simplifié pour le salon logs (1440720849375068170)"""
    if improvements is None:
        improvements = []
    
    # Déterminer le type de changement principal et construire description
    all_items = []
    
    # Section AJOUTS
    if added_cmds:
        for cmd in added_cmds:
            all_items.append(f"✨ Nouvelle commande: /{cmd}")
    
    # Section SUPPRESSIONS
    if removed_cmds:
        for cmd in removed_cmds:
            all_items.append(f"⛔ Commande supprimée: /{cmd}")
    
    # Section MODIFICATIONS/AMÉLIORATIONS
    if improvements:
        for improvement in improvements:
            # Ajouter un bullet point si pas déjà présent
            if not improvement.startswith("•"):
                all_items.append(f"• {improvement}")
            else:
                all_items.append(improvement)
    
    # Déterminer titre et couleur selon le type de changement
    if improvements and not added_cmds and not removed_cmds:
        color = 0xffa500  # Orange - MODIFICATIONS
        title = "✨ Nouvelles Fonctionnalités:"
    elif added_cmds and not removed_cmds and not improvements:
        color = 0x00ff00  # Vert - AJOUTS
        title = "✨ Nouvelles Commandes:"
    elif removed_cmds and not added_cmds and not improvements:
        color = 0xff0000  # Rouge - SUPPRESSIONS
        title = "⛔ Commandes Supprimées:"
    else:
        color = 0x5865f2  # Bleu - MIXTE
        title = "🔄 Changements Détectés:"
    
    # Description avec tous les changements en bullet points
    description = "\n".join(all_items[:15])
    if len(all_items) > 15:
        description += f"\n... et **{len(all_items) - 15} autres** changements"
    
    embed = discord.Embed(
        title=title,
        description=description,
        color=color,
        timestamp=discord.utils.utcnow()
    )
    
    embed.set_footer(text=f"🐱 Miaouscle Bot • v{version}")
    return embed

def create_embed_added(commands: List[str], version: str) -> discord.Embed:
    """Crée un embed pour les commandes ajoutées"""
    embed = discord.Embed(
        title="✨ Nouvelles Commandes Ajoutées",
        color=0x00ff00,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Miaouscle Bot v{version}")
    
    description = "\n".join([f"• /{cmd}" for cmd in commands[:10]])
    embed.description = description
    
    if len(commands) > 10:
        embed.description += f"\n... et {len(commands) - 10} autres"
    
    return embed

def create_embed_removed(commands: List[str], version: str) -> discord.Embed:
    """Crée un embed pour les commandes supprimées"""
    embed = discord.Embed(
        title="⛔ Commandes Supprimées",
        color=0xff0000,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Miaouscle Bot v{version}")
    
    description = "\n".join([f"• /{cmd}" for cmd in commands[:10]])
    embed.description = description
    
    if len(commands) > 10:
        embed.description += f"\n... et {len(commands) - 10} autres"
    
    return embed

def create_embed_modified(added_cmds: List[str], removed_cmds: List[str], version: str) -> discord.Embed:
    """Crée un embed pour les modifications mixtes"""
    embed = discord.Embed(
        title="🔧 Modifications du Bot",
        color=0xffa500,
        timestamp=discord.utils.utcnow()
    )
    embed.set_footer(text=f"Miaouscle Bot v{version}")
    
    if added_cmds:
        added_text = "\n".join([f"✨ /{cmd}" for cmd in added_cmds[:5]])
        embed.add_field(name="Commandes Ajoutées", value=added_text, inline=False)
        if len(added_cmds) > 5:
            embed.add_field(name="", value=f"... et {len(added_cmds) - 5} autres", inline=False)
    
    if removed_cmds:
        removed_text = "\n".join([f"⛔ /{cmd}" for cmd in removed_cmds[:5]])
        embed.add_field(name="Commandes Supprimées", value=removed_text, inline=False)
        if len(removed_cmds) > 5:
            embed.add_field(name="", value=f"... et {len(removed_cmds) - 5} autres", inline=False)
    
    return embed

def check_and_update() -> Tuple[bool, Optional[discord.Embed], Optional[str]]:
    """
    Vérifie les changements de commandes et retourne (has_changes, embed_log, new_version)
    embed_log: Pour le salon logs unique (1440720849375068170)
    """
    current_hashes = get_all_file_hashes()
    current_contents = get_all_file_contents()
    last_hashes = load_last_hashes()
    backups = load_backups()
    current_version = get_current_version()
    
    # Premier démarrage
    if not last_hashes:
        print("📝 Première initialisation - sauvegarde des fichiers...")
        save_hashes(current_hashes)
        save_backups(current_contents)
        save_version(current_version)
        return False, None, None
    
    # Comparaison des changements RÉELS
    all_added = []
    all_removed = []
    changed_files = []
    
    for filepath, current_hash in current_hashes.items():
        last_hash = last_hashes.get(filepath, "")
        current_content = current_contents.get(filepath, "")
        old_content = backups.get(filepath, "")
        
        # Si le hash a changé
        if current_hash != last_hash and last_hash and old_content:
            changed_files.append(filepath)
            
            # Vérifier les changements de commandes
            added, removed = detect_command_changes(old_content, current_content)
            if added or removed:
                all_added.extend(added)
                all_removed.extend(removed)
    
    # Détecter aussi les améliorations et changements de code
    all_improvements: List[str] = []
    for filepath in changed_files:
        old_content = backups.get(filepath, "")
        current_content = current_contents.get(filepath, "")
        improvements = detect_improvements(old_content, current_content, filepath)
        if improvements:
            all_improvements.extend(improvements)
    
    # S'il y a des changements de commandes OU des améliorations
    if all_added or all_removed or all_improvements:
        # Incrémenter la version
        new_version = increment_version(current_version)
        save_version(new_version)
        
        # Sauvegarder les nouveaux fichiers
        save_hashes(current_hashes)
        save_backups(current_contents)
        
        # Créer SEULEMENT l'embed LOG avec détails clairs
        embed_log = create_log_embed(all_added, all_removed, new_version, all_improvements)
        
        print(f"✨ Nouvelles commandes: {', '.join(all_added) if all_added else 'aucune'}")
        print(f"⛔ Commandes supprimées: {', '.join(all_removed) if all_removed else 'aucune'}")
        print(f"💪 Améliorations détectées: {', '.join(all_improvements) if all_improvements else 'aucune'}")
        print(f"🔔 Changements détectés! Version: {current_version} → {new_version}")
        return True, embed_log, new_version
    else:
        # Pas de changements - juste sauvegarder pour la prochaine fois
        save_hashes(current_hashes)
        save_backups(current_contents)
        return False, None, None
