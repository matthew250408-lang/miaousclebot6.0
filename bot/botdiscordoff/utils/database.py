import json
import os
import random
import string
from datetime import datetime

# ----------------------------
# Initialisation
# ----------------------------
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DATA_FOLDER = os.path.join(_PROJECT_ROOT, "data")
os.makedirs(DATA_FOLDER, exist_ok=True)

# Chemin centralisé pour la config (accessible au bot ET au dashboard)
CONFIG_PATH = os.path.join(_PROJECT_ROOT, "web", "data", "server_configs.json")
os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)

# ----------------------------
# CONFIGURATION
# ----------------------------
def load_config(guild_id):
    """Charger la configuration d'un serveur (depuis le store PostgreSQL persistant,
    avec fallback JSON si DB indisponible). Voir utils/server_config_store.py."""
    try:
        from utils.server_config_store import load_one
        cfg = load_one(guild_id)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        try:
            with open(CONFIG_PATH, 'r', encoding='utf-8') as f:
                data = json.load(f)
            return data.get(str(guild_id), {}) if isinstance(data, dict) else {}
        except (FileNotFoundError, json.JSONDecodeError):
            return {}

def save_config(guild_id, config):
    """Sauvegarder la configuration d'un serveur dans PostgreSQL (source de
    vérité unique depuis v13.2.0). Lève si la DB est down — on préfère
    échouer bruyamment plutôt qu'écrire dans un JSON local qui ne serait
    jamais réconcilié vers la DB. Voir utils/server_config_store.py."""
    if not isinstance(config, dict):
        config = {}
    from utils.server_config_store import save_one
    save_one(guild_id, config)

# ----------------------------
# LEVELS / XP
# ----------------------------
def get_user_level(guild_id, user_id):
    """Obtenir les données de niveau d'un utilisateur"""
    path = os.path.join(DATA_FOLDER, "levels.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(str(guild_id), {}).get(str(user_id), {'xp': 0})
    except (FileNotFoundError, json.JSONDecodeError):
        return {'xp': 0}

def add_xp(guild_id, user_id, xp_amount):
    """Ajouter de l'XP à un utilisateur"""
    path = os.path.join(DATA_FOLDER, "levels.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    guild_data = data.setdefault(str(guild_id), {})
    user_data = guild_data.setdefault(str(user_id), {'xp': 0})

    user_data['xp'] += xp_amount

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return user_data['xp']

def remove_xp(guild_id, user_id, xp_amount):
    """Retirer de l'XP à un utilisateur"""
    path = os.path.join(DATA_FOLDER, "levels.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    guild_data = data.setdefault(str(guild_id), {})
    user_data = guild_data.setdefault(str(user_id), {'xp': 0})

    current_xp = user_data['xp']
    user_data['xp'] = max(0, current_xp - xp_amount)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return user_data['xp']

def get_leaderboard(guild_id, limit=10):
    """Obtenir le classement d'un serveur"""
    path = os.path.join(DATA_FOLDER, "levels.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        guild_data = data.get(str(guild_id), {})
        sorted_users = sorted(
            guild_data.items(),
            key=lambda x: x[1].get('xp', 0),
            reverse=True
        )
        return [(int(user_id), user_data) for user_id, user_data in sorted_users[:limit]]
    except (FileNotFoundError, json.JSONDecodeError):
        return []

# ----------------------------
# WARNINGS / SANCTIONS
# ----------------------------
def generate_sanction_id(length=5):
    """Générer un ID unique pour une sanction (ex: 1UYU3)"""
    return ''.join(random.choices(string.ascii_uppercase + string.digits, k=length))

def add_warning(guild_id, user_id, moderator_id, reason, action_type="warn", sanction_id=None):
    """Ajouter une sanction (avertissement, mute, ban, etc.)"""
    path = os.path.join(DATA_FOLDER, "warnings.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}

    guild_data = data.setdefault(str(guild_id), {})
    user_data = guild_data.setdefault(str(user_id), [])

    sanction_id = sanction_id or generate_sanction_id()

    warning = {
        'id': sanction_id,
        'type': action_type,
        'moderator_id': moderator_id,
        'reason': reason,
        'timestamp': datetime.utcnow().isoformat()
    }

    user_data.append(warning)

    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return sanction_id

def get_warnings(guild_id, user_id):
    """Obtenir les avertissements d'un utilisateur"""
    path = os.path.join(DATA_FOLDER, "warnings.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        return data.get(str(guild_id), {}).get(str(user_id), [])
    except (FileNotFoundError, json.JSONDecodeError):
        return []

# ----------------------------
# XP JOURNALIER
# ----------------------------
def load_daily_xp():
    """Charger les données d'XP journalier"""
    path = os.path.join(DATA_FOLDER, "daily_xp.json")
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

def save_daily_xp(data):
    """Sauvegarder les données d'XP journalier"""
    path = os.path.join(DATA_FOLDER, "daily_xp.json")
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
