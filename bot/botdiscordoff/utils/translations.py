
"""
Système de traductions pour Miaouscle Bot 💪😺
Gère les traductions multilingues (FR / EN / ES / DE)
"""

import json
import os
import logging
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# ------------------------
# Langues supportées
# ------------------------
SUPPORTED_LANGUAGES = {
    'fr': '🇫🇷 Français',
    'en': '🇺🇸 English',
    'es': '🇪🇸 Español',
    'de': '🇩🇪 Deutsch'
}

DEFAULT_LANGUAGE = 'fr'


class TranslationManager:
    def __init__(self):
        self.translations: Dict[str, Dict] = {}
        self.load_translations()

    # ------------------------
    # Chargement des fichiers JSON
    # ------------------------
    def load_translations(self):
        """Charge toutes les traductions disponibles depuis le dossier /translations"""
        translations_dir = 'translations'
        os.makedirs(translations_dir, exist_ok=True)

        for lang_code in SUPPORTED_LANGUAGES:
            file_path = os.path.join(translations_dir, f"{lang_code}.json")
            try:
                if os.path.exists(file_path):
                    with open(file_path, 'r', encoding='utf-8') as f:
                        self.translations[lang_code] = json.load(f)
                        logger.info(f"✅ Traduction chargée : {lang_code}.json ({SUPPORTED_LANGUAGES[lang_code]})")
                else:
                    self.translations[lang_code] = {}
                    logger.warning(f"⚠️ Fichier de traduction manquant : {lang_code}.json")
            except Exception as e:
                logger.error(f"❌ Erreur lors du chargement de {lang_code}.json : {e}")
                self.translations[lang_code] = {}

    # ------------------------
    # Récupération de texte traduit
    # ------------------------
    def get_text(self, guild_id: int, key: str, default: Optional[str] = None, **kwargs) -> str:
        """
        Récupère un texte traduit selon la langue du serveur.
        Si la clé ou la langue n'existe pas, revient sur la langue par défaut.
        """
        from utils.database import load_config

        # Langue du serveur (ou par défaut)
        config = load_config(guild_id)
        language = config.get('language', DEFAULT_LANGUAGE)

        # Cherche le texte dans la langue du serveur
        text = self._get_nested_value(self.translations.get(language, {}), key)

        # Fallback → français si non trouvé
        if not text:
            text = self._get_nested_value(self.translations.get(DEFAULT_LANGUAGE, {}), key)

        # Si toujours pas trouvé → utilise le default ou renvoie la clé
        if not text:
            if default:
                return default
            logger.warning(f"Clé de traduction introuvable : {key}")
            return f"[{key}]"

        # Injection de variables dynamiques
        try:
            return text.format(**kwargs)
        except KeyError as e:
            logger.error(f"Variable manquante dans la traduction {key} : {e}")
            return f"[{key}]"

    # ------------------------
    # Accès clé imbriquée
    # ------------------------
    def _get_nested_value(self, data: Dict, key: str) -> str:
        """Récupère une valeur dans un dictionnaire imbriqué via une clé de type 'commands.help.title'"""
        # D'abord essayer la clé plate directement (ex: "levels.leaderboard.title")
        if key in data and isinstance(data[key], str):
            return data[key]
        
        # Ensuite essayer la structure imbriquée
        current = data
        for part in key.split('.'):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                return ""
        return current if isinstance(current, str) else ""

    # ------------------------
    # Outils
    # ------------------------
    def get_available_languages(self) -> Dict[str, str]:
        """Retourne la liste des langues disponibles"""
        return SUPPORTED_LANGUAGES


# ------------------------
# Fonctions globales pratiques
# ------------------------
translator = TranslationManager()


def get_text(guild_id: int, key: str, default: Optional[str] = None, **kwargs) -> str:
    """Accès simplifié à la traduction"""
    return translator.get_text(guild_id, key, default=default, **kwargs)


def get_guild_language(guild_id: int) -> str:
    """Récupère la langue configurée pour un serveur"""
    from utils.database import load_config
    config = load_config(guild_id)
    return config.get('language', DEFAULT_LANGUAGE)


def set_guild_language(guild_id: int, language: str) -> bool:
    """Définit la langue pour un serveur"""
    if language not in SUPPORTED_LANGUAGES:
        logger.warning(f"Tentative de définir une langue non supportée : {language}")
        return False

    from utils.database import load_config, save_config
    config = load_config(guild_id)
    config['language'] = language
    save_config(guild_id, config)
    logger.info(f"🌐 Langue du serveur {guild_id} définie sur {language}")
    return True
