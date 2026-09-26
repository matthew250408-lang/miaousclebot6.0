"""
Miaouscle Bot - Main Module
Gestion du bot Discord: initialisation, événements, maintenance, logs auto, bienvenue/au revoir, XP.
Version: v8.3.0
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands, ui
import json
import os
import logging
from dotenv import load_dotenv
import hashlib
import traceback
from typing import Union, Mapping, Optional, Dict, Any, cast
from enum import Enum
from datetime import datetime, timezone
import asyncio
from utils.translations import get_text
from utils.database import load_config, save_config, add_xp, get_user_level
from utils.server_config_store import init_schema as _init_server_config_store
from utils.singleton_lock import acquire_or_exit as _acquire_singleton
from utils.auto_changelog import check_and_update

# Init du store persistant (PostgreSQL + migration auto depuis le JSON legacy
# au premier boot). Idempotent : ne fait rien si déjà initialisé.
_init_server_config_store()

# Verrou singleton : empêche deux instances du bot de tourner avec le même token
# (sinon Discord envoie chaque event aux deux gateways → doublons de welcome,
# logs, XP, etc.). Si une autre instance a déjà la lock, ce process exit.
_acquire_singleton()
import random
import math

# Stats tracking avec noms
def track_stats(guild_id: int, user_id: int, command: str, user_name: str = None):
    """Enregistre l'utilisation des commandes avec noms"""
    try:
        _project_root = os.path.dirname(os.path.abspath(__file__))
        stats_file = os.path.join(_project_root, 'web', 'data', 'stats.json')
        users_map_file = os.path.join(_project_root, 'web', 'data', 'users_map.json')
        os.makedirs(os.path.dirname(stats_file), exist_ok=True)
        
        # Charger stats
        if os.path.exists(stats_file):
            with open(stats_file, 'r') as f:
                stats = json.load(f)
        else:
            stats = {'servers': {}, 'users': {}, 'commands': {}}
        
        # Charger map users (ID → pseudo)
        if os.path.exists(users_map_file):
            with open(users_map_file, 'r') as f:
                users_map = json.load(f)
        else:
            users_map = {}
        
        guild_name = str(guild_id)
        user_id_str = str(user_id)
        cmd_name = command.split()[0]
        
        # Tracker stats
        stats['servers'][guild_name] = stats['servers'].get(guild_name, 0) + 1
        stats['users'][user_id_str] = stats['users'].get(user_id_str, 0) + 1
        stats['commands'][cmd_name] = stats['commands'].get(cmd_name, 0) + 1
        
        # Sauvegarder user name si fourni
        if user_name and user_id_str not in users_map:
            users_map[user_id_str] = user_name
        
        with open(stats_file, 'w') as f:
            json.dump(stats, f, indent=2)
        with open(users_map_file, 'w') as f:
            json.dump(users_map, f, indent=2)
    except Exception:
        pass

# ============================================================================
# CONFIGURATION & ENVIRONMENT
# ============================================================================
load_dotenv()
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Configuration constantes
OWNER_ID = 1140296215469240401
XP_MIN = 10
XP_MAX = 15
LOG_CHANNEL_NAME = "📂・logs-miaouscle-bot"
SUPPORT_SERVER_URL = "https://discord.gg/faZYKM52"

# ============================================================================
# ENUMS
# ============================================================================
class MaintenanceMode(Enum):
    """Modes de maintenance du bot."""
    ON = True
    OFF = False


class EventType(Enum):
    """Types d'événements Discord."""
    MEMBER_JOIN = "join"
    MEMBER_LEAVE = "leave"
    GUILD_JOIN = "guild_join"
    GUILD_REMOVE = "guild_remove"
    LEVEL_UP = "level_up"


# Couleurs pour les embeds
EMBED_COLORS = {
    "maintenance_on": 0xffcc00,
    "maintenance_off": 0x00ff99,
    "welcome": 0x00ff00,
    "goodbye": 0xff9900,
    "level_up": 0xffd700,
    "help": 0x0099ff,
    "error": 0xff0000,
}

# ============================================================================
# DISCORD BOT INITIALIZATION
# ============================================================================
intents = discord.Intents.default()
intents.message_content = True
intents.members = True

bot = commands.Bot(command_prefix="!", intents=intents, help_command=None)

# Variables globales
MAINTENANCE = MaintenanceMode.OFF.value
READY_SETUP_CALLED = False
_config_cache: Dict[int, Dict[str, Any]] = {}
AUTO_CHANGE_EMBED = None  # Seulement le log embed pour le canal 1440720849375068170

# Attributs du bot
bot.maintenance_mode = MAINTENANCE  # type: ignore
bot.version = None  # type: ignore


# ============================================================================
# VERSION MANAGEMENT
# ============================================================================
def get_change_description() -> str:
    """Génère une description précise des changements depuis le CHANGELOG."""
    try:
        if not os.path.exists("CHANGELOG.md"):
            return "Modifications détectées (CHANGELOG non trouvé)"
        
        with open("CHANGELOG.md", "r", encoding="utf-8") as f:
            lines = f.readlines()
        
        description_parts = []
        in_features = False
        in_bugs = False
        in_security = False
        found_version = False
        features = []
        bugs = []
        security = []
        
        for line in lines:
            line = line.rstrip("\n")
            
            # Trouver la première version après le titre
            if line.startswith("## Version") and not found_version:
                found_version = True
                continue
            
            # Si on a trouvé la version, parser ses sections
            if found_version:
                if line.startswith("---"):
                    # Fin de cette version
                    break
                
                # Détecter les sections
                if "### ✨ Nouvelles Fonctionnalités" in line:
                    in_features, in_bugs, in_security = True, False, False
                elif "### 🐛 Corrections de Bugs" in line:
                    in_features, in_bugs, in_security = False, True, False
                elif "### 🛡️ Améliations de Sécurité" in line:
                    in_features, in_bugs, in_security = False, False, True
                elif line.startswith("### "):
                    # Autre section
                    in_features = in_bugs = in_security = False
                
                # Collecter les items
                if line.startswith("- ") and not line.startswith("---"):
                    item = line[2:].strip()
                    if in_features:
                        features.append(item)
                    elif in_bugs:
                        bugs.append(item)
                    elif in_security:
                        security.append(item)
        
        # Construire la description
        if features:
            description_parts.append("✨ **Nouvelles Fonctionnalités:**")
            for feat in features[:3]:
                description_parts.append(f"  • {feat}")
        
        if bugs:
            description_parts.append("\n🐛 **Corrections de Bugs:**")
            for bug in bugs[:2]:
                description_parts.append(f"  • {bug}")
        
        if security:
            description_parts.append("\n🛡️ **Améliations de Sécurité:**")
            for sec in security[:2]:
                description_parts.append(f"  • {sec}")
        
        return "\n".join(description_parts) if description_parts else "Modifications détectées"
    
    except Exception as e:
        print(f"⚠️ Erreur parsing changelog: {e}")
        return "Modifications détectées"


def save_current_hash() -> None:
    """Sauvegarde le hash actuel des fichiers (marque les modifications comme traitées)."""
    hash_file = "last_hash.txt"
    hash_md5 = hashlib.md5()
    
    # Hash du code (cogs + main.py + utils)
    cogs_path = os.path.join(os.path.dirname(__file__), "cogs")
    if os.path.exists(cogs_path):
        for root, _, files in os.walk(cogs_path):
            for f in sorted(files):
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), "rb") as file:
                            hash_md5.update(file.read())
                    except (IOError, OSError):
                        pass
    
    utils_path = os.path.join(os.path.dirname(__file__), "utils")
    if os.path.exists(utils_path):
        for root, _, files in os.walk(utils_path):
            for f in sorted(files):
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), "rb") as file:
                            hash_md5.update(file.read())
                    except (IOError, OSError):
                        pass

    try:
        with open(__file__, "rb") as f:
            hash_md5.update(f.read())
    except (IOError, OSError):
        pass
    
    # Hash des traductions
    translations_path = os.path.join(os.path.dirname(__file__), "translations")
    if os.path.exists(translations_path):
        for f in sorted(["fr.json", "en.json", "es.json", "de.json"]):
            try:
                with open(os.path.join(translations_path, f), "rb") as file:
                    hash_md5.update(file.read())
            except (IOError, OSError):
                pass
    
    # Hash du CHANGELOG
    changelog_file = os.path.join(os.path.dirname(__file__), "CHANGELOG.md")
    if os.path.exists(changelog_file):
        try:
            with open(changelog_file, "rb") as file:
                hash_md5.update(file.read())
        except (IOError, OSError):
            pass

    current_hash = hash_md5.hexdigest()
    
    try:
        with open(hash_file, "w") as f:
            f.write(current_hash)
    except (IOError, OSError):
        pass


def get_and_increment_version() -> tuple[str, bool]:
    """Récupère et incrémente la version du bot. Retourne (version, has_changed)."""
    version_file = "version.txt"
    hash_file = "last_hash.txt"

    # Lire ou créer version initiale
    if not os.path.exists(version_file):
        with open(version_file, "w") as f:
            f.write("8.3.0")
        version = "8.3.0"
    else:
        with open(version_file, "r") as f:
            version = f.read().strip()

    try:
        major, minor, patch = map(int, version.split("."))
    except ValueError:
        major, minor, patch = 8, 3, 0

    # Calculer le hash des fichiers (code + traductions)
    hash_md5 = hashlib.md5()
    
    # Hash du code (cogs + main.py + utils)
    cogs_path = os.path.join(os.path.dirname(__file__), "cogs")
    if os.path.exists(cogs_path):
        for root, _, files in os.walk(cogs_path):
            for f in sorted(files):
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), "rb") as file:
                            hash_md5.update(file.read())
                    except (IOError, OSError):
                        pass
    
    utils_path = os.path.join(os.path.dirname(__file__), "utils")
    if os.path.exists(utils_path):
        for root, _, files in os.walk(utils_path):
            for f in sorted(files):
                if f.endswith(".py"):
                    try:
                        with open(os.path.join(root, f), "rb") as file:
                            hash_md5.update(file.read())
                    except (IOError, OSError):
                        pass

    try:
        with open(__file__, "rb") as f:
            hash_md5.update(f.read())
    except (IOError, OSError):
        pass
    
    # Hash des traductions
    translations_path = os.path.join(os.path.dirname(__file__), "translations")
    if os.path.exists(translations_path):
        for f in sorted(["fr.json", "en.json", "es.json", "de.json"]):
            try:
                with open(os.path.join(translations_path, f), "rb") as file:
                    hash_md5.update(file.read())
            except (IOError, OSError):
                pass
    
    # Hash du CHANGELOG
    changelog_file = os.path.join(os.path.dirname(__file__), "CHANGELOG.md")
    if os.path.exists(changelog_file):
        try:
            with open(changelog_file, "rb") as file:
                hash_md5.update(file.read())
        except (IOError, OSError):
            pass

    current_hash = hash_md5.hexdigest()

    # Lire le hash précédent
    last_hash = ""
    if os.path.exists(hash_file):
        try:
            with open(hash_file, "r") as f:
                last_hash = f.read().strip()
        except (IOError, OSError):
            pass

    # Incrémenter la version SEULEMENT si changement détecté
    version_changed = False
    if current_hash != last_hash:
        # Code a changé - incrémenter la version
        minor += 1
        patch = 0
        version_changed = True
        new_version = f"{major}.{minor}.{patch}"
        
        # Sauvegarder la nouvelle version et le hash
        try:
            with open(version_file, "w") as f:
                f.write(new_version)
        except (IOError, OSError):
            pass
        
        try:
            with open(hash_file, "w") as f:
                f.write(current_hash)
        except (IOError, OSError):
            pass
    else:
        # Code n'a pas changé - garder la même version
        new_version = f"{major}.{minor}.{patch}"

    return new_version, version_changed


# Initialiser avec version de base - sera mis à jour par auto_changelog
def load_bot_version() -> str:
    """Charge la version depuis le fichier version.json"""
    try:
        with open("version.json", "r") as f:
            data = json.load(f)
            return data.get("version", "v11.43.0").replace("v", "")
    except:
        return "11.43.0"

BOT_VERSION = load_bot_version()
print(f"🚀 Démarrage du bot — v{BOT_VERSION}")


# ============================================================================
# CHANGELOG PARSING
# ============================================================================
def parse_changelog() -> Optional[Dict[str, str]]:
    """Parse le CHANGELOG.md et retourne la version la plus récente avec ses détails."""
    try:
        if not os.path.exists("CHANGELOG.md"):
            return None
        
        with open("CHANGELOG.md", "r", encoding="utf-8") as f:
            content = f.read()
        
        lines = content.split("\n")
        version = None
        features = []
        bugs = []
        security = []
        
        for line in lines:
            if line.startswith("## Version"):
                version = line.split("Version ")[1].split(" -")[0].strip()
                break
        
        in_features = False
        in_bugs = False
        in_security = False
        
        # Trouver le bloc de la version la plus récente
        version_block = []
        found_first_version = False
        for line in lines:
            if line.startswith("## Version"):
                if not found_first_version:
                    found_first_version = True
                else:
                    break
            if found_first_version:
                version_block.append(line)
        
        for line in version_block:
            if "### ✨ Nouvelles Fonctionnalités" in line:
                in_features, in_bugs, in_security = True, False, False
            elif "### 🐛 Corrections de Bugs" in line:
                in_features, in_bugs, in_security = False, True, False
            elif "### 🛡️ Améliations de Sécurité" in line:
                in_features, in_bugs, in_security = False, False, True
            
            if line.startswith("- ") and not line.startswith("---"):
                item = line[2:].strip()
                # Détecter si c'est BOT ou SITE
                prefix = ""
                if "[BOT]" in item:
                    prefix = "🤖 "
                elif "[SITE]" in item:
                    prefix = "🌐 "
                
                clean_item = item.replace("[BOT]", "").replace("[SITE]", "").strip()
                formatted_item = f"{prefix}{clean_item}"
                
                if in_features:
                    features.append(formatted_item)
                elif in_bugs:
                    bugs.append(formatted_item)
                elif in_security:
                    security.append(formatted_item)
        
        return {
            "version": version or "?.?.?",
            "features": "\n".join(features) if features else "Aucune nouveauté.",
            "bugs": "\n".join(bugs) if bugs else "Aucun correctif.",
            "security": "\n".join(security) if security else "Aucune.",
        }
    except Exception as e:
        print(f"⚠️ Erreur parsing changelog: {e}")
        return None


# ============================================================================
# HELPER FUNCTIONS - CACHING & CONFIGURATION
# ============================================================================
_config_cache: Dict[int, Dict[str, Any]] = {}  # Note: cache is bypassed in get_config_cached for real-time sync

def get_config_cached(guild_id: int) -> Dict[str, Any]:
    """Récupère la configuration (cache désactivé pour synchro immédiate)."""
    return load_config(guild_id)

def clear_config_cache(guild_id: int) -> None:
    """Efface le cache pour un serveur (obsolète mais gardé pour compatibilité)."""
    if guild_id in _config_cache:
        del _config_cache[guild_id]


async def get_log_channel_safe() -> Optional[discord.TextChannel]:
    """Récupère le canal de logs de manière sûre."""
    try:
        with open("config.json", "r") as f:
            config = json.load(f)
        log_channel_id = config.get("log_channel_id", 0)
        if log_channel_id:
            ch = bot.get_channel(int(log_channel_id))
            if isinstance(ch, discord.TextChannel):
                return ch
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        pass
    return None


async def update_bot_status() -> None:
    """Met à jour le statut du bot selon le mode maintenance."""
    status = discord.Status.idle if MAINTENANCE else discord.Status.online
    activity = discord.Game(
        f"🔧 Maintenance (v{BOT_VERSION})" if MAINTENANCE 
        else f"v{BOT_VERSION}"
    )
    await bot.change_presence(status=status, activity=activity)


# ============================================================================
# MAINTENANCE MODE
# ============================================================================
async def check_maintenance_global(interaction: discord.Interaction) -> bool:
    """Check global pour bloquer les slash commands en maintenance."""
    ALLOWED_IN_MAINTENANCE = {"maintenance", "report", "support", "help", "mode-emploi", "ping"}
    
    if interaction.command and interaction.command.name in ALLOWED_IN_MAINTENANCE:
        return True
    
    if MAINTENANCE and interaction.user.id != OWNER_ID:
        raise app_commands.CheckFailure(
            get_text(0, "main.maintenance.all_disabled", default="🔧 Maintenance mode enabled.")
        )
    
    return True


# Ajouter le check global
bot.tree.interaction_check = check_maintenance_global


@bot.check
def global_prefix_check(ctx: commands.Context) -> bool:
    """Check pour les commandes préfixées."""
    if MAINTENANCE and ctx.author.id != OWNER_ID:
        raise commands.CheckFailure(
            get_text(0, "main.maintenance.all_disabled", default="Maintenance mode enabled.")
        )
    return True


async def announce_maintenance(status: bool) -> None:
    """Annonce le changement de maintenance."""
    log_channel = await get_log_channel_safe()
    owner = bot.get_user(OWNER_ID)

    color = EMBED_COLORS["maintenance_on"] if status else EMBED_COLORS["maintenance_off"]
    title = get_text(0, "main.maintenance.mode_enabled", default="🔧 Maintenance Mode")
    
    embed = discord.Embed(
        title=title,
        description=(
            "⚠️ **Le bot est maintenant en maintenance !**" 
            if status 
            else "✅ **Le bot est de nouveau opérationnel !**"
        ),
        color=color,
        timestamp=datetime.now(timezone.utc),
    )
    embed.set_footer(text=f"Version {BOT_VERSION}")

    if log_channel:
        try:
            await log_channel.send(embed=embed)
        except discord.DiscordException:
            pass

    if owner:
        try:
            await owner.send(embed=embed)
        except discord.DiscordException:
            pass

    print(f"🪛 Maintenance : {'ON' if status else 'OFF'}")


def sync_maintenance_to_site(is_active: bool, show_reason: bool = True, custom_message: str = None) -> None:
    """Synchronise le statut maintenance avec le site - STRUCTURE SÉPARÉE bot + site."""
    try:
        _project_root = os.path.dirname(os.path.abspath(__file__))
        _web_data_dir = os.path.join(_project_root, 'web', 'data')
        os.makedirs(_web_data_dir, exist_ok=True)
        maintenance_file = os.path.join(_web_data_dir, 'maintenance.json')
        
        # Lire le fichier actuel pour préserver l'état du site
        site_data = {"active": False, "message": "", "start_time": None, "end_time": None, "show_reason": True}
        try:
            with open(maintenance_file, 'r', encoding='utf-8') as f:
                current = json.load(f)
                if isinstance(current, dict) and 'site' in current:
                    site_data = current['site']
        except:
            pass
        
        # Construire la nouvelle structure
        message = ""
        if is_active:
            message = custom_message if custom_message else "🔧 Maintenance en cours - Nous travaillons à l'amélioration du service"
        
        maintenance_data = {
            "bot": {
                "active": is_active,
                "message": message,
                "start_time": datetime.now(timezone.utc).isoformat() if is_active else None,
                "end_time": None,
                "show_reason": show_reason
            },
            "site": site_data  # Préserver l'état du site
        }
        
        with open(maintenance_file, 'w', encoding='utf-8') as f:
            json.dump(maintenance_data, f, indent=2, ensure_ascii=False)
        
        print(f"✅ Maintenance synced (bot: {is_active}, site: {site_data.get('active', False)}, type: {'both' if is_active and site_data.get('active') else ('bot' if is_active else ('site' if site_data.get('active') else 'none'))})")
    except Exception:
        pass


# ============================================================================
# MAINTENANCE COMMAND
# ============================================================================
class MaintenanceCog(commands.Cog):
    """Cog pour la commande de maintenance."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="maintenance", description="Activer ou désactiver le mode maintenance du bot")
    @app_commands.describe(mode="on pour activer, off pour désactiver")
    @app_commands.choices(mode=[
        app_commands.Choice(name="Activer la maintenance", value="on"),
        app_commands.Choice(name="Désactiver la maintenance", value="off"),
    ])
    async def maintenance(self, interaction: discord.Interaction, mode: str):
        """Active ou désactive le mode maintenance du bot."""
        global MAINTENANCE

        OWNER_IDS = {653038666625785866, 1140296215469240401}
        is_owner = interaction.user.id in OWNER_IDS
        is_admin = interaction.guild and isinstance(interaction.user, discord.Member) and interaction.user.guild_permissions.administrator

        if not is_owner and not is_admin:
            await interaction.response.send_message("❌ Vous devez être administrateur pour utiliser cette commande.", ephemeral=True)
            return

        await interaction.response.defer()

        if mode == "on":
            MAINTENANCE = True
            bot.maintenance_mode = True
            sync_maintenance_to_site(True)
            await update_bot_status()
            embed = discord.Embed(
                title="🔧 Bot en Maintenance",
                description="Le bot est maintenant en mode maintenance. Les commandes sont désactivées.",
                color=0xffcc00,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Utilisez /maintenance mode:off pour désactiver")
        else:
            MAINTENANCE = False
            bot.maintenance_mode = False
            sync_maintenance_to_site(False)
            await update_bot_status()
            embed = discord.Embed(
                title="✅ Maintenance Terminée",
                description="Le bot est de retour en ligne. Toutes les commandes sont à nouveau disponibles.",
                color=0x00ff99,
                timestamp=datetime.now(timezone.utc)
            )

        try:
            await interaction.followup.send(embed=embed)
        except discord.NotFound:
            pass


async def setup_maintenance_cog(bot: commands.Bot) -> None:
    """Charge le cog de maintenance."""
    await bot.add_cog(MaintenanceCog(bot))


# ============================================================================
# COG LOADING
# ============================================================================
async def load_cogs() -> None:
    """Charge tous les cogs disponibles."""
    from utils.logger import send_bug_notification

    cogs_dir = os.path.join(os.path.dirname(__file__), "cogs")
    if not os.path.exists(cogs_dir):
        print("⚠️ Dossier cogs/ non trouvé.")
        return

    loaded_cogs: list[str] = []
    failed_cogs: list[tuple[str, str]] = []

    for filename in os.listdir(cogs_dir):
        if filename.endswith(".py") and not filename.startswith("__"):
            try:
                await bot.load_extension(f"cogs.{filename[:-3]}")
                print(f"✅ Cog chargé : {filename}")
                loaded_cogs.append(filename)
            except Exception as e:
                print(f"❌ Erreur cog {filename} : {e}")
                failed_cogs.append((filename, str(e)))

    # Notifier si des cogs ont échoué
    if failed_cogs:
        error_msg = "\n".join(f"• **{c}** : ```{e}```" for c, e in failed_cogs)
        await send_bug_notification(
            bot,
            "ERROR",
            f"Erreur lors du chargement des cogs ({len(failed_cogs)})",
            f"❌ {len(failed_cogs)} cogs n'ont pas pu être chargés :\n{error_msg}",
            "error",
        )


# ============================================================================
# LOG CHANNEL MANAGEMENT
# ============================================================================
async def setup_log_channels_all_guilds() -> None:
    """Crée les salons de logs dans tous les serveurs et envoie un message traduit."""
    print(f"🔍 Création des salons de logs pour {len(bot.guilds)} serveurs...")
    
    for guild in bot.guilds:
        try:
            # Créer le salon de logs s'il n'existe pas
            log_channel = await create_log_channel(guild)
            if not log_channel:
                continue
            
            # Vérifier si un message a déjà été envoyé (chercher un message du bot)
            if bot.user:
                async for message in log_channel.history(limit=10):
                    if message.author and message.author.id == bot.user.id and message.embeds:
                        embed_title = message.embeds[0].title or ""
                        if "Salon des logs" in embed_title:
                            print(f"✅ Salon déjà configuré pour {guild.name}")
                            break
            else:
                # Aucun message trouvé, envoyer le message
                config = get_config_cached(guild.id)
                lang = config.get("language", "fr")
                guild_id = guild.id
                
                # Créer le message traduit
                embed = discord.Embed(
                    title=get_text(guild_id, "main.logs.log_channel_created", default="📝 Salon des Logs"),
                    color=EMBED_COLORS["welcome"],
                    timestamp=datetime.now(timezone.utc),
                )
                
                # Messages traduits pour chaque langue
                descriptions = {
                    "fr": "📝 **Bienvenue dans le salon des logs de Miaouscle Bot !**\n\nCe salon enregistre **automatiquement**:\n• Les avertissements et sanctions\n• Les bans, mutes, kicks\n• Les modérations effectuées\n• Les mises à jour du bot\n\n**Seuls les administrateurs et modérateurs peuvent voir ce salon.**",
                    "en": "📝 **Welcome to Miaouscle Bot's log channel!**\n\nThis channel automatically records:\n• Warnings and sanctions\n• Bans, mutes, kicks\n• Moderation actions\n• Bot updates\n\n**Only administrators and moderators can see this channel.**",
                    "es": "📝 **¡Bienvenido al canal de registros de Miaouscle Bot!**\n\nEste canal registra automáticamente:\n• Advertencias y sanciones\n• Prohibiciones, silencios, expulsiones\n• Acciones de moderación\n• Actualizaciones del bot\n\n**Solo administradores y moderadores pueden ver este canal.**",
                    "de": "📝 **Willkommen im Protokollkanal von Miaouscle Bot!**\n\nDieser Kanal zeichnet automatisch auf:\n• Verwarnungen und Sanktionen\n• Verbote, Stummschaltungen, Ausweisungen\n• Moderationsmaßnahmen\n• Bot-Updates\n\n**Nur Administratoren und Moderatoren können diesen Kanal sehen.**"
                }
                
                embed.description = descriptions.get(lang, descriptions["fr"])
                embed.set_footer(text=f"Miaouscle Bot v{BOT_VERSION}")
                
                await log_channel.send(embed=embed)
                print(f"📢 Message de logs envoyé pour {guild.name} (langue: {lang})")
                
        except Exception as e:
            print(f"❌ Erreur setup logs pour {guild.name}: {e}")


async def create_log_channel(guild: discord.Guild) -> Optional[discord.TextChannel]:
    """Crée un canal de logs privé pour le serveur."""
    config = get_config_cached(guild.id)

    # Vérifier si le canal existe déjà
    log_channel_id: Optional[int] = config.get("log_channel")
    if log_channel_id:
        try:
            channel_id_int = int(log_channel_id) if log_channel_id else 0
            existing = guild.get_channel(channel_id_int)
            if existing and isinstance(existing, discord.TextChannel):
                print(f"✅ Salon de logs déjà existant pour {guild.name}")
                return existing
        except (ValueError, TypeError):
            pass

    try:
        # Créer les permissions
        overwrites: Dict[Union[discord.Role, discord.Member], discord.PermissionOverwrite] = {
            guild.default_role: discord.PermissionOverwrite(read_messages=False),
            guild.me: discord.PermissionOverwrite(
                read_messages=True, send_messages=True, embed_links=True
            ),
        }

        # Ajouter permissions pour admins/modos
        for role in guild.roles:
            if (
                role.permissions.administrator
                or role.permissions.ban_members
                or role.permissions.kick_members
                or role.permissions.manage_messages
            ):
                overwrites[role] = discord.PermissionOverwrite(
                    read_messages=True, send_messages=True
                )

        # Créer le canal
        log_channel = await guild.create_text_channel(
            name=LOG_CHANNEL_NAME,
            topic="Permet de voir les modifications apportées par miaouscle bot",
            overwrites=cast(Mapping, overwrites),
            reason="Création automatique du salon de logs par Miaouscle Bot",
        )

        # Sauvegarder dans la config
        config["log_channel"] = log_channel.id
        save_config(guild.id, config)
        clear_config_cache(guild.id)

        # Envoyer message de bienvenue
        embed = discord.Embed(
            title=get_text(0, "main.logs.log_channel_created", default="📝 Logs Channel Created"),
            description=(
                "Bienvenue dans le salon de logs **privé** de **Miaouscle Bot** !\n\n"
                "Ce salon enregistre toutes les actions de modération effectuées par le bot.\n\n"
                "**Seuls les administrateurs et modérateurs peuvent voir ce salon.**"
            ),
            color=EMBED_COLORS["welcome"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Miaouscle Bot v{BOT_VERSION}")

        mention = guild.owner.mention if guild.owner else ""
        await log_channel.send(content=mention, embed=embed)
        print(f"✅ Salon de logs privé créé pour {guild.name}")
        return log_channel

    except discord.Forbidden:
        print(f"❌ Permissions insuffisantes pour créer le salon de logs sur {guild.name}")
        return None
    except Exception as e:
        print(f"❌ Erreur création salon de logs pour {guild.name}: {e}")
        return None


# ============================================================================
# WELCOME & GOODBYE EVENTS
# ============================================================================
def _parse_color(value, fallback: int) -> int:
    """Parse une couleur (hex string '#RRGGBB' ou int) avec fallback."""
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.strip():
        try:
            return int(value.strip().lstrip("#"), 16)
        except ValueError:
            pass
    return fallback


def _apply_placeholders(text: str, member: discord.Member) -> str:
    """Remplace {user}, {username}, {guild}, {count} dans un texte."""
    if not isinstance(text, str):
        return text
    return (
        text.replace("{user}", member.mention)
            .replace("{username}", member.name)
            .replace("{guild}", member.guild.name)
            .replace("{count}", str(member.guild.member_count or 0))
    )


async def send_welcome_message(member: discord.Member) -> None:
    """Envoie un message de bienvenue (avec personnalisation Premium si activée)."""
    from utils.logger import send_bug_notification
    from utils.premium import has_premium, TIER_PREMIUM, TIER_PREMIUM_PLUS

    try:
        config = get_config_cached(member.guild.id)
        welcome_channel_id = config.get("welcome_channel")

        if not welcome_channel_id:
            print(f"⚠️ Pas de salon de bienvenue configuré pour {member.guild.name}")
            return

        welcome_channel = member.guild.get_channel(int(welcome_channel_id))
        if not welcome_channel or not isinstance(welcome_channel, discord.TextChannel):
            await send_bug_notification(
                bot,
                "BUG",
                "Salon de bienvenue introuvable",
                f"Le salon de bienvenue (ID: {welcome_channel_id}) n'existe plus pour **{member.guild.name}**.",
                "error",
            )
            return

        # Tier Premium effectif (serveur OU owner)
        owner_id = member.guild.owner_id or 0
        is_premium = has_premium(member.guild.id, owner_id, TIER_PREMIUM)
        is_premium_plus = has_premium(member.guild.id, owner_id, TIER_PREMIUM_PLUS)

        # Description (placeholders {user}, {username}, {guild}, {count})
        default_message = get_text(
            member.guild.id,
            "events.welcome.default_message",
            user=member.name,
            guild=member.guild.name,
            default=f"Welcome {member.name} to {member.guild.name}!",
        )
        welcome_message = _apply_placeholders(
            config.get("welcome_message") or default_message, member
        )

        # Titre — personnalisable Premium
        default_title = get_text(member.guild.id, "events.welcome.title", default="🎉 Welcome!")
        title = (config.get("welcome_title") if is_premium else None) or default_title
        title = _apply_placeholders(title, member)

        # Couleur — personnalisable Premium
        color = _parse_color(
            config.get("welcome_color") if is_premium else None,
            EMBED_COLORS["welcome"],
        )

        embed = discord.Embed(
            title=title,
            description=welcome_message,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(
            url=member.avatar.url if member.avatar else member.default_avatar.url
        )

        # Image bannière — Premium
        if is_premium:
            image_url = config.get("welcome_image")
            if image_url and isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                embed.set_image(url=image_url)

        # Footer — personnalisable Premium
        custom_footer = config.get("welcome_footer") if is_premium else None
        if custom_footer:
            embed.set_footer(text=_apply_placeholders(custom_footer, member))
        else:
            member_count_text = get_text(
                member.guild.id,
                "events.welcome.member_count",
                count=member.guild.member_count,
                default=f"Member #{member.guild.member_count}",
            )
            embed.set_footer(text=member_count_text)

        # Ping — toggle Premium (par défaut activé)
        ping_enabled = True
        if is_premium and "welcome_ping" in config:
            ping_enabled = bool(config.get("welcome_ping"))
        content = f"🎉 {member.mention}" if ping_enabled else None

        await welcome_channel.send(content=content, embed=embed)
        print(f"✅ Message de bienvenue envoyé pour {member.name}")

        # MP de bienvenue — Premium+
        if is_premium_plus and config.get("welcome_dm_enabled"):
            dm_text = config.get("welcome_dm_message") or welcome_message
            dm_text = _apply_placeholders(dm_text, member)
            try:
                dm_embed = discord.Embed(
                    title=title,
                    description=dm_text,
                    color=color,
                    timestamp=datetime.now(timezone.utc),
                )
                await member.send(embed=dm_embed)
                print(f"✅ MP de bienvenue envoyé à {member.name}")
            except (discord.Forbidden, discord.HTTPException) as dm_err:
                print(f"⚠️ Impossible d'envoyer le MP de bienvenue à {member.name}: {dm_err}")

    except Exception as e:
        print(f"❌ Erreur message de bienvenue : {e}")


async def send_goodbye_message(member: discord.Member) -> None:
    """Envoie un message d'au revoir (avec personnalisation Premium si activée)."""
    from utils.logger import send_bug_notification
    from utils.premium import has_premium, TIER_PREMIUM

    try:
        config = get_config_cached(member.guild.id)
        goodbye_channel_id = config.get("goodbye_channel")

        if not goodbye_channel_id:
            print(f"⚠️ Pas de salon d'au revoir configuré pour {member.guild.name}")
            return

        goodbye_channel = member.guild.get_channel(int(goodbye_channel_id))
        if not goodbye_channel or not isinstance(goodbye_channel, discord.TextChannel):
            await send_bug_notification(
                bot,
                "BUG",
                "Salon d'au revoir introuvable",
                f"Le salon d'au revoir (ID: {goodbye_channel_id}) n'existe plus pour **{member.guild.name}**.",
                "error",
            )
            return

        owner_id = member.guild.owner_id or 0
        is_premium = has_premium(member.guild.id, owner_id, TIER_PREMIUM)

        # Description (placeholders)
        default_message = get_text(
            member.guild.id,
            "events.goodbye.default_message",
            user=member.name,
            guild=member.guild.name,
            default=f"{member.name} left {member.guild.name}.",
        )
        goodbye_message = _apply_placeholders(
            config.get("goodbye_message") or default_message, member
        )

        # Titre — Premium
        default_title = get_text(member.guild.id, "events.goodbye.title", default="👋 Goodbye!")
        title = (config.get("goodbye_title") if is_premium else None) or default_title
        title = _apply_placeholders(title, member)

        # Couleur — Premium
        color = _parse_color(
            config.get("goodbye_color") if is_premium else None,
            EMBED_COLORS["goodbye"],
        )

        embed = discord.Embed(
            title=title,
            description=goodbye_message,
            color=color,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_thumbnail(
            url=member.avatar.url if member.avatar else member.default_avatar.url
        )

        # Image — Premium
        if is_premium:
            image_url = config.get("goodbye_image")
            if image_url and isinstance(image_url, str) and image_url.startswith(("http://", "https://")):
                embed.set_image(url=image_url)

        # Footer — Premium
        custom_footer = config.get("goodbye_footer") if is_premium else None
        if custom_footer:
            embed.set_footer(text=_apply_placeholders(custom_footer, member))
        else:
            member_count_text = get_text(
                member.guild.id,
                "events.goodbye.member_count",
                count=member.guild.member_count,
                default=f"{member.guild.member_count} members left",
            )
            embed.set_footer(text=member_count_text)

        # Ping — Premium (off par défaut côté goodbye, mais on garde l'option)
        ping_enabled = False
        if is_premium and "goodbye_ping" in config:
            ping_enabled = bool(config.get("goodbye_ping"))
        content = f"👋 {member.mention}" if ping_enabled else None

        await goodbye_channel.send(content=content, embed=embed)
        print(f"✅ Message d'au revoir envoyé pour {member.name}")

    except Exception as e:
        print(f"❌ Erreur message d'au revoir : {e}")


async def create_mention_response(guild_id: int) -> discord.Embed:
    """Crée l'embed simple de démarrage quand le bot est mentionné."""
    embed = discord.Embed(
        title=get_text(guild_id, "main.mention.title", default="Salut! 👋"),
        description=get_text(guild_id, "main.mention.description", default="Je suis **Miaouscle Bot** - Ton bot Discord complet! 🐱"),
        color=EMBED_COLORS["help"],
    )
    
    embed.add_field(
        name=get_text(guild_id, "main.mention.quick_start", default="📍 Commandes rapides"),
        value=get_text(guild_id, "main.mention.quick_commands", default="`/help` pour voir toutes les commandes\n`/mode-emploi` pour le guide complet\n`/support` si tu as besoin d'aide"),
        inline=False,
    )
    
    embed.add_field(
        name="🚀 Version",
        value=f"v{BOT_VERSION}",
        inline=True,
    )
    
    embed.set_footer(text=get_text(guild_id, "main.mention.footer", default="💪😺"))
    
    return embed


# ============================================================================
# ERROR HANDLERS
# ============================================================================
def _is_unknown_interaction_error(error: Exception) -> bool:
    """Détecte une erreur Discord 'Unknown interaction' (10062), même wrappée
    par CommandInvokeError. Cause : latence Replit ↔ Discord trop élevée
    (>3s) avant le premier accusé de réception. Inéluctable côté code,
    on ne notifie pas comme un vrai bug.
    """
    seen = set()
    current: BaseException | None = error
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, discord.NotFound) and getattr(current, "code", None) == 10062:
            return True
        if isinstance(current, discord.HTTPException) and getattr(current, "code", None) == 10062:
            return True
        current = getattr(current, "__cause__", None) or getattr(current, "__context__", None)
    return False


@bot.tree.error
async def slash_error(interaction: discord.Interaction, error: Exception) -> None:
    """Gère les erreurs des slash commands."""
    from utils.logger import send_bug_notification

    if isinstance(error, app_commands.CheckFailure):
        embed = discord.Embed(
            title=get_text(0, "main.maintenance.mode_enabled", default="🔧 Maintenance"),
            description=str(error),
            color=EMBED_COLORS["maintenance_on"],
        )
        try:
            if not interaction.response.is_done():
                await interaction.response.send_message(embed=embed, ephemeral=True)
            else:
                await interaction.followup.send(embed=embed, ephemeral=True)
        except discord.DiscordException:
            pass
        return

    command = interaction.command
    cmd_name = command.qualified_name if command else "Unknown"

    # Erreurs « Unknown interaction » (10062) : Discord a déjà invalidé le
    # token avant que le bot puisse répondre (latence > 3s). Aucune action
    # corrective possible côté code une fois que c'est arrivé. On ignore
    # silencieusement (pas de log, pas de notif bug) pour ne pas polluer
    # les logs à chaque pic de latence Replit ↔ Discord.
    if _is_unknown_interaction_error(error):
        return

    # `traceback.format_exc()` est vide ici lorsque discord.py transmet
    # l'exception au handler hors du bloc `except` d'origine. Reconstituer la
    # trace depuis l'objet reçu permet d'identifier les erreurs de
    # transformation d'arguments (`_transform_arguments`) avec leur cause
    # réelle.
    error_traceback = ''.join(
        traceback.format_exception(type(error), error, error.__traceback__)
    )
    guild_name = getattr(interaction.guild, "name", None) or "DM"
    guild_id = getattr(interaction.guild, "id", None) or "n/a"
    user_id = getattr(interaction.user, "id", "n/a")
    print(
        f"❌ Erreur slash non gérée | command={cmd_name} "
        f"| guild={guild_name} ({guild_id}) | user={user_id} "
        f"| type={type(error).__name__}: {error}\n{error_traceback}"
    )

    await send_bug_notification(
        bot,
        "ERROR",
        f"❌ Erreur slash command: `{cmd_name}`\n"
        f"**Type:** `{type(error).__name__}`\n"
        f"**Erreur:** {str(error)[:200]}",
        f"```python\n{error_traceback[-3500:]}\n```",
        "error",
        user=interaction.user,
        guild=interaction.guild,
        command=cmd_name,
    )


@bot.event
async def on_command_error(ctx: commands.Context, error: Exception) -> None:
    """Gère les erreurs des commandes préfixées."""
    from utils.logger import send_bug_notification

    if isinstance(error, commands.CheckFailure):
        embed = discord.Embed(
            title=get_text(0, "main.maintenance.mode_enabled", default="🔧 Maintenance"),
            description=str(error),
            color=EMBED_COLORS["maintenance_on"],
        )
        try:
            await ctx.reply(embed=embed, mention_author=False)
        except discord.DiscordException:
            pass
        return

    error_traceback = traceback.format_exc()
    cmd_name = getattr(ctx.command, "name", str(ctx.command))
    print(f"Erreur commande '{cmd_name}': {error}")
    
    await send_bug_notification(
        bot,
        "ERROR",
        f"❌ Erreur commande préfixée: `{cmd_name}`\n**Erreur:** {str(error)[:200]}",
        f"```python\n{error_traceback[:1000]}\n```",
        "error",
        user=ctx.author,
        guild=ctx.guild,
        command=cmd_name,
    )


@bot.event
async def on_error(event: str, *args: Any, **kwargs: Any) -> None:
    """Gestionnaire d'erreurs global pour les événements."""
    from utils.logger import send_bug_notification

    error_msg = traceback.format_exc()
    print(f"❌ Erreur dans {event}: {error_msg}")

    await send_bug_notification(
        bot,
        "BUG",
        f"Erreur dans l'événement {event}",
        f"```python\n{error_msg[:1800]}\n```",
        "error",
    )


# ============================================================================
# DISCORD EVENTS
# ============================================================================
@bot.event
async def on_guild_join(guild: discord.Guild) -> None:
    """Événement déclenché quand le bot rejoint un serveur."""
    from utils.logger import send_bug_notification

    print(f"🎉 Bot ajouté au serveur : {guild.name} (ID: {guild.id})")

    await send_bug_notification(
        bot,
        "MODIFICATION",
        "Bot ajouté à un nouveau serveur",
        f"🎉 Le bot a été ajouté au serveur **{guild.name}**\n"
        f"• ID: `{guild.id}`\n"
        f"• Propriétaire: {guild.owner.mention if guild.owner else 'Inconnu'}\n"
        f"• Membres: {guild.member_count}",
        "success",
    )

    # Mettre à jour immédiatement la liste des serveurs
    await export_bot_servers()

    # Créer le salon de logs
    await create_log_channel(guild)

    # Envoyer message au propriétaire
    if guild.owner:
        try:
            embed = discord.Embed(
                title=get_text(0, "main.welcome.thanks_title", default="👋 Thanks for adding me!"),
                description=(
                    f"Bienvenue sur **{guild.name}** ! Je suis ravi de rejoindre ton serveur.\n\n"
                    f"Je suis un bot Discord complet avec **système de niveaux**, **modération avancée**, "
                    f"**configuration par serveur**, et **support multilingue** (FR/EN/ES/DE)."
                ),
                color=EMBED_COLORS["help"],
                timestamp=datetime.now(timezone.utc),
            )

            embed.add_field(
                name="🚀 Démarrage Rapide",
                value=(
                    "• `/help` - Voir toutes les commandes disponibles\n"
                    "• `/mode-emploi` - Guide complet d'utilisation\n"
                    "• `/config` - Personnaliser le bot pour ton serveur\n"
                    "• `/ping` - Vérifier que je fonctionne"
                ),
                inline=False,
            )

            embed.add_field(
                name="📊 Fonctionnalités Principales",
                value=(
                    "✅ **Système de Niveaux** - Gagne 10-15 XP par message\n"
                    "✅ **Modération Complète** - Avertissements, mutes, bans...\n"
                    "✅ **Configurations Avancées** - Salons spécialisés, messages personnalisés\n"
                    "✅ **Logs Automatiques** - Salon privé pour les actions de modération\n"
                    "✅ **Multilingue** - Français, English, Español, Deutsch"
                ),
                inline=False,
            )

            embed.set_footer(text=f"Miaouscle Bot v{BOT_VERSION} • Merci de ta confiance! 💪😺")

            await guild.owner.send(embed=embed)
            print(f"✅ Message de remerciement envoyé au propriétaire de {guild.name}")
        except discord.DiscordException as e:
            print(f"❌ Impossible d'envoyer message au propriétaire: {e}")


@bot.event
async def on_guild_remove(guild: discord.Guild) -> None:
    """Événement déclenché quand le bot quitte un serveur."""
    guild_id = guild.id
    guild_name = guild.name

    print(f"👋 Bot retiré du serveur : {guild_name} (ID: {guild_id})")

    try:
        config = get_config_cached(guild_id)
        log_channel_id = config.get("log_channel")

        # Supprimer le salon de logs
        if log_channel_id:
            try:
                log_channel = guild.get_channel(int(log_channel_id))
                if log_channel and isinstance(log_channel, discord.TextChannel):
                    await log_channel.delete(reason="Bot retiré du serveur - Nettoyage automatique")
                    print(f"✅ Salon de logs supprimé pour {guild_name}")
            except (discord.Forbidden, discord.NotFound, ValueError):
                pass

        # Nettoyer la configuration
        save_config(guild_id, {})
        clear_config_cache(guild_id)
        print(f"✅ Nettoyage effectué pour {guild_name}")

    except Exception as e:
        print(f"❌ Erreur nettoyage pour {guild_name}: {e}")

    # Mettre à jour immédiatement la liste des serveurs
    await export_bot_servers()


@bot.event
async def on_member_join(member: discord.Member) -> None:
    """Événement déclenché quand un membre rejoint le serveur."""
    await send_welcome_message(member)


@bot.event
async def on_member_remove(member: discord.Member) -> None:
    """Événement déclenché quand un membre quitte le serveur."""
    await send_goodbye_message(member)


@bot.event
async def on_message(message: discord.Message) -> None:
    """Gère les messages: XP, mentions du bot, commandes préfixées."""
    if message.author.bot or not message.guild:
        return

    # Répondre si mention seule du bot
    if (
        bot.user
        and bot.user in message.mentions
        and not message.mention_everyone
        and len(message.mentions) == 1
        and message.content.strip() in [f"<@{bot.user.id}>", f"<@!{bot.user.id}>"]
    ):
        guild_id = message.guild.id if message.guild else 0
        embed = await create_mention_response(guild_id)
        if bot.user and bot.user.avatar:
            embed.set_thumbnail(url=bot.user.avatar.url)
        await message.reply(embed=embed)

    # Traiter les commandes préfixées
    try:
        await bot.process_commands(message)
    except Exception as e:
        print(f"❌ Erreur traitement commande : {e}")
        traceback.print_exception(type(e), e, e.__traceback__)


# ============================================================================
# BOT STARTUP
# ============================================================================
def _sync_load_json(filename, default):
    try:
        with open(filename, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


def _sync_save_json(filename, data, ensure_dir=None):
    if ensure_dir:
        os.makedirs(ensure_dir, exist_ok=True)
    with open(filename, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


async def async_load_json(filename, default=None):
    """Lecture JSON non bloquante (exécutée dans un thread worker)."""
    return await asyncio.to_thread(_sync_load_json, filename, default if default is not None else {})


async def async_save_json(filename, data, ensure_dir=None):
    """Écriture JSON non bloquante (exécutée dans un thread worker)."""
    await asyncio.to_thread(_sync_save_json, filename, data, ensure_dir)


@tasks.loop(seconds=2)
async def process_feedback_queue() -> None:
    """Traite la queue de feedback (reports/suggestions) toutes les 2 secondes."""
    from cogs.general import SuggestionView

    REPORT_CHANNEL_ID = 1332455504777318561
    SUGGESTION_CHANNEL_ID = 1344414298847576104

    try:
        queue = await async_load_json('feedback_queue.json', {})
        
        for timestamp, item in list(queue.items()):
            try:
                channel_id = SUGGESTION_CHANNEL_ID if item['type'] == 'suggestion' else REPORT_CHANNEL_ID
                channel = bot.get_channel(channel_id)
                if not channel:
                    channel = await bot.fetch_channel(channel_id)
                
                if not channel or not isinstance(channel, discord.TextChannel):
                    continue
                
                # Créer l'embed
                if item['type'] == 'report':
                    color = 0xFF0000
                    title = "🚨 Nouveau signalement"
                    fields = [
                        ("👤 Utilisateur", item.get('discord', 'N/A')),
                        ("🏠 Lieu", "Site Web"),
                        ("📋 Raison", item.get('description', 'N/A')),
                    ]
                else:
                    color = 0xFFD700
                    title = "💡 Nouvelle suggestion"
                    fields = [
                        ("👤 Utilisateur", item.get('discord', 'N/A')),
                        ("🏠 Lieu", "Site Web"),
                        ("📋 Catégorie", item.get('category', 'N/A')),
                        ("💡 Suggestion", item.get('description', 'N/A')),
                    ]
                
                embed = discord.Embed(
                    title=title,
                    color=color,
                    timestamp=datetime.now(timezone.utc)
                )
                
                for field_name, field_value in fields:
                    embed.add_field(name=field_name, value=field_value, inline=False)
                
                embed.set_footer(text="Help Menu")
                
                # Envoyer avec boutons pour les suggestions
                if item['type'] == 'suggestion':
                    view = SuggestionView(bot, bot.user)
                    msg = await channel.send(embed=embed, view=view)
                    view.message = msg
                else:
                    await channel.send(embed=embed)
                
                # Supprimer de la queue
                del queue[timestamp]
                await async_save_json('feedback_queue.json', queue)

            except Exception as e:
                print(f"❌ Erreur traitement feedback {timestamp}: {e}")
                continue
    
    except Exception as e:
        print(f"❌ Erreur queue feedback: {e}")


@tasks.loop(seconds=5)
async def process_broadcast_queue() -> None:
    """Traite la queue de broadcasts toutes les 5 secondes."""
    try:
        queue = await async_load_json('web/data/broadcast_queue.json', [])
        if not isinstance(queue, list):
            queue = []
        
        for i, broadcast in enumerate(list(queue)):
            if broadcast.get('status') != 'pending':
                continue
            
            try:
                title = broadcast.get('title', '📢 Broadcast')
                message = broadcast.get('message', '')
                
                # Créer l'embed stylisé
                embed = discord.Embed(
                    title=f"📢 {title}",
                    description=message,
                    color=0x667eea,
                    timestamp=discord.utils.utcnow()
                )
                embed.set_footer(text="Message du Super Admin")
                
                # Envoyer à tous les serveurs
                sent_count = 0
                for guild in bot.guilds:
                    try:
                        # Chercher le premier canal texte
                        text_channel = None
                        for channel in guild.text_channels:
                            if channel.permissions_for(guild.me).send_messages:
                                text_channel = channel
                                break
                        
                        if text_channel:
                            await text_channel.send(embed=embed)
                            sent_count += 1
                    except Exception as e:
                        print(f"❌ Erreur envoi broadcast à {guild.name}: {e}")
                        continue
                
                print(f"✅ Broadcast '{title}' envoyé à {sent_count} serveurs")
                
                # Marquer comme envoyé
                queue[i]['status'] = 'sent'
                queue[i]['sent_to'] = sent_count
                await async_save_json('web/data/broadcast_queue.json', queue, ensure_dir='web/data')
                
            except Exception as e:
                print(f"❌ Erreur traitement broadcast: {e}")
                continue
    
    except Exception as e:
        print(f"❌ Erreur process_broadcast_queue: {e}")


def _read_bot_maintenance_from_file() -> bool | None:
    """Lit web/data/maintenance.json et retourne l'état bot.active.
    Retourne None si le fichier est absent ou illisible.
    """
    try:
        _project_root = os.path.dirname(os.path.abspath(__file__))
        _maint_path = os.path.join(_project_root, 'web', 'data', 'maintenance.json')
        with open(_maint_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if isinstance(data, dict):
            bot_section = data.get('bot')
            if isinstance(bot_section, dict):
                return bool(bot_section.get('active', False))
    except (IOError, OSError, json.JSONDecodeError, ValueError):
        pass
    return None


@tasks.loop(seconds=10)
async def check_site_maintenance() -> None:
    """Synchronise bidirectionnellement la maintenance bot avec le JSON.

    - Si l'admin a activé/désactivé la maintenance bot via le dashboard
      (modification du JSON), on bascule MAINTENANCE et on met à jour le
      statut Discord (idle/online) immédiatement.
    - On RE-FORCE le statut Discord à chaque tick si la maintenance est
      active : Discord peut « oublier » la présence après une reconnexion
      gateway, ce qui faisait apparaître le bot comme « En ligne » alors
      qu'il devait être « Inactif ».
    - On pousse aussi MAINTENANCE → site pour préserver la cohérence si
      la commande slash /maintenance a modifié l'état côté bot.
    """
    global MAINTENANCE
    file_state = _read_bot_maintenance_from_file()
    state_changed = file_state is not None and file_state != bool(MAINTENANCE)

    if state_changed:
        MAINTENANCE = file_state
        bot.maintenance_mode = file_state  # type: ignore
        # Reset le compteur de re-poste à chaque bascule pour un comportement
        # temporel prévisible (la 1ère re-pose en mode ON arrive ~60s après).
        check_site_maintenance._tick = 0  # type: ignore
        try:
            await update_bot_status()
            print(f"🔧 Maintenance basculée: {file_state} → statut Discord mis à jour")
        except discord.DiscordException as e:
            print(f"⚠️  Impossible de mettre à jour le statut Discord : {e}")
        try:
            await announce_maintenance(file_state)
        except Exception as e:
            print(f"⚠️  Annonce maintenance échouée : {e}")
    elif MAINTENANCE:
        # Re-pose le statut idle environ 1 fois par minute (vs toutes les 10s
        # pour respecter le rate limit Discord ≈ 5 change_presence / 60s).
        # Compense les cas où Discord oublie la présence (gateway resume).
        check_site_maintenance._tick = getattr(check_site_maintenance, "_tick", 0) + 1
        if check_site_maintenance._tick % 6 == 0:
            try:
                await update_bot_status()
            except discord.DiscordException:
                pass

    sync_maintenance_to_site(MAINTENANCE)


@bot.event
async def on_resumed() -> None:
    """Re-applique le statut du bot après une reconnexion à la gateway Discord.

    Sans ça, Discord peut afficher le bot comme « En ligne » par défaut
    après un resume, même si MAINTENANCE est True côté bot. Combiné au
    re-poste idempotent dans `check_site_maintenance`, ça garantit que le
    statut « Inactif » en maintenance est toujours visible.
    """
    try:
        await update_bot_status()
        if MAINTENANCE:
            print("🔌 Gateway resumed → statut Discord (idle / maintenance) re-appliqué")
    except discord.DiscordException as e:
        print(f"⚠️  on_resumed : update_bot_status échec : {e}")


async def on_ready_setup() -> None:
    """Initialisation du bot au démarrage."""
    global AUTO_CHANGE_DESCRIPTION, AUTO_CHANGE_EMBED
    from utils.logger import send_bug_notification

    global MAINTENANCE

    print(f"✅ Bot prêt : {bot.user}")

    # Ré-hydratation : si la maintenance bot était activée via le dashboard
    # avant le redémarrage, on restaure l'état depuis le fichier JSON pour
    # que le statut Discord (idle) soit correct dès le boot.
    file_state = _read_bot_maintenance_from_file()
    if file_state is not None:
        MAINTENANCE = file_state
        bot.maintenance_mode = file_state  # type: ignore

    # Garde défensive : une erreur Discord transitoire ici ne doit pas
    # interrompre le chargement des cogs. La task check_site_maintenance
    # (toutes les 10 s) re-tentera la mise à jour du statut.
    try:
        await update_bot_status()
    except discord.DiscordException as e:
        print(f"⚠️  update_bot_status() au boot a échoué : {e} — la task de maintenance retentera.")
    await load_cogs()
    
    # Démarrer le traitement de la queue
    try:
        process_feedback_queue.start()
    except RuntimeError:
        pass  # Déjà en cours d'exécution
    
    # Démarrer le traitement des broadcasts
    try:
        process_broadcast_queue.start()
    except RuntimeError:
        pass  # Déjà en cours d'exécution
    
    # Démarrer la vérification de maintenance du site
    try:
        check_site_maintenance.start()
    except RuntimeError:
        pass  # Déjà en cours d'exécution

    # Ajouter le cog de maintenance
    try:
        await setup_maintenance_cog(bot)
    except Exception:
        pass

    # Réinitialiser avant de vérifier les changements
    AUTO_CHANGE_EMBED = None

    # Vérifier les changements automatiquement (SILENCIEUX)
    global BOT_VERSION
    has_changes, embed_log, new_version = check_and_update()
    print(f"🔍 Check changements: has_changes={has_changes}, embed_log={embed_log is not None}, new_version={new_version}")
    if has_changes and embed_log and new_version:
        AUTO_CHANGE_EMBED = embed_log
        BOT_VERSION = new_version.replace("v", "")
        print(f"✅ AUTO_CHANGE_EMBED défini avec succès")

    # Auto-sync versionné : si la version courante diffère de celle déjà syncée
    # avec Discord, on resync automatiquement (global + serveur de support en
    # mode instantané). Évite les CommandSignatureMismatch après chaque release.
    sync_enabled = os.getenv("SYNC_COMMANDS", "false").lower() in {"1", "true", "yes", "on"}
    _sync_marker_path = os.path.join(
        os.path.dirname(os.path.abspath(__file__)), ".last_synced_version"
    )
    last_synced_version = None
    try:
        if os.path.exists(_sync_marker_path):
            with open(_sync_marker_path, "r", encoding="utf-8") as _f:
                last_synced_version = _f.read().strip() or None
    except Exception as _e:
        print(f"⚠️ Lecture {_sync_marker_path} échouée : {_e}")
    version_changed = last_synced_version != BOT_VERSION
    if version_changed and not sync_enabled:
        print(f"🆕 Nouvelle version détectée ({last_synced_version} → {BOT_VERSION}) — sync auto activé.")
        sync_enabled = True

    synced_count = len(bot.tree.get_commands())
    try:
        if sync_enabled:
            # ⚠️ IMPORTANT — éviter les commandes en doublon sur le serveur de
            # support : les versions précédentes faisaient `copy_global_to` +
            # `sync(guild=support)` après le sync global, ce qui causait Discord
            # à afficher CHAQUE commande deux fois (une globale + une guild-
            # specific). On nettoie d'abord les copies guild-specific avant de
            # ne refaire QUE le sync global.
            try:
                from utils.premium import SUPPORT_GUILD_ID
                support_guild = discord.Object(id=SUPPORT_GUILD_ID)
                bot.tree.clear_commands(guild=support_guild)
                cleared = await bot.tree.sync(guild=support_guild)
                print(f"🧹 Commandes guild-specific nettoyées sur le serveur de support "
                      f"({SUPPORT_GUILD_ID}) — {len(cleared)} commandes restantes côté guild.")
            except Exception as _e:
                print(f"⚠️ Nettoyage guild support échoué (non bloquant) : {_e}")

            print("🔄 Sync des commandes en cours...")
            synced = await bot.tree.sync()
            synced_count = len(synced)
            print(f"🌐 {synced_count} commandes slash synchronisées globalement "
                  f"(propagation Discord ~1h).")

            # Marquer la version comme syncée pour ne pas re-sync au prochain restart
            try:
                with open(_sync_marker_path, "w", encoding="utf-8") as _f:
                    _f.write(BOT_VERSION)
            except Exception as _e:
                print(f"⚠️ Écriture {_sync_marker_path} échouée : {_e}")
        else:
            print(f"⏭️ Sync commandes slash ignorée (version {BOT_VERSION} déjà synchronisée) — {synced_count} commandes chargées localement.")

        total_members = sum(g.member_count or 0 for g in bot.guilds)
        
        # Attendre un peu pour que le bot soit complètement prêt
        print("⏳ Attente 1s avant envoi des notifications...")
        await asyncio.sleep(1)
        
        # Envoyer l'embed de démarrage
        print("📤 Envoi de l'embed de démarrage...")
        startup_description = (
            f"✅ **Le bot a démarré avec succès !**\n"
            f"• **{synced_count}** commandes chargées\n"
            f"• **{len(bot.guilds)}** serveurs connectés\n"
            f"• **{total_members:,}** membres en total"
        )
        await send_bug_notification(
            bot,
            "SUCCESS",
            f"🚀 Miaouscle Bot v{BOT_VERSION} Démarré",
            startup_description,
            "success",
        )
        print("✅ Embed de démarrage envoyé")
        
        # Envoyer le log des modifications SEULEMENT au canal logs (SI changements auto-détectés)
        if AUTO_CHANGE_EMBED:
            await asyncio.sleep(0.5)
            try:
                # Envoyer l'embed LOG SEULEMENT dans le canal de logs (1440720849375068170)
                log_channel = bot.get_channel(1440720849375068170)
                if not log_channel:
                    log_channel = await bot.fetch_channel(1440720849375068170)
                if log_channel and isinstance(log_channel, discord.TextChannel):
                    await log_channel.send(embed=AUTO_CHANGE_EMBED)
                    print(f"✅ Log technique envoyé au canal logs (1440720849375068170)")
            except Exception as e:
                print(f"❌ Erreur envoi modifications: {e}")
        
        
    except discord.HTTPException as e:
        if getattr(e, "code", None) == 30034:
            print("⚠️ Quota Discord atteint: sync commandes slash ignorée pour aujourd'hui (limite 200 créations/jour).")
            return
        print(f"❌ Erreur sync globale : {e}")
        import traceback
        traceback.print_exc()
    except Exception as e:
        print(f"❌ Erreur sync globale : {e}")
        import traceback
        traceback.print_exc()
        try:
            await send_bug_notification(
                bot,
                "ERROR",
                "⚠️ Erreur lors du démarrage du bot",
                f"❌ Erreur sync commandes :\n```{str(e)}```",
                "error",
            )
        except Exception as notify_err:
            print(f"❌ Erreur lors de l'envoi de l'erreur : {notify_err}")


async def export_bot_servers() -> None:
    """Exporte la liste des serveurs du bot dans all_bot_servers.json."""
    try:
        all_servers = []
        for guild in bot.guilds:
            try:
                owner = guild.owner
                owner_name = owner.display_name if owner else "Inconnu"
                all_servers.append({
                    'id': str(guild.id),
                    'name': guild.name,
                    'members': guild.member_count or 0,
                    'owner_id': str(guild.owner_id) if guild.owner_id else 'N/A',
                    'owner_name': owner_name,
                    'bot_present': True
                })
            except Exception:
                pass
        os.makedirs('web/data', exist_ok=True)
        with open('web/data/all_bot_servers.json', 'w', encoding='utf-8') as f:
            json.dump(all_servers, f, indent=2, ensure_ascii=False)
        print(f"✅ {len(all_servers)} serveurs exportés dans all_bot_servers.json")
    except Exception as e:
        print(f"❌ Erreur export serveurs: {e}")


@bot.event
async def on_ready() -> None:
    """Événement déclenché quand le bot est prêt."""
    global READY_SETUP_CALLED
    try:
        print(f"✅ Bot {bot.user} connecté!")

        if not READY_SETUP_CALLED:
            READY_SETUP_CALLED = True
            print("🔄 Appel on_ready_setup()...")
            await on_ready_setup()
        
        # Exporter TOUS les serveurs du bot dans un fichier JSON
        await export_bot_servers()
        # Lancer le traitement des suggestions du site
        if not process_site_suggestions.is_running():
            process_site_suggestions.start()
    except Exception as e:
        print(f"❌ Erreur dans on_ready: {e}")
        import traceback
        traceback.print_exc()


# ============================================================================
# LISTENER FOR SITE SUGGESTIONS
# ============================================================================
class SitesuggestionView(ui.View):
    """Boutons pour les suggestions du site web"""
    def __init__(self, user_id: int, suggestion_id: str):
        super().__init__(timeout=None)
        self.user_id = user_id
        self.suggestion_id = suggestion_id

    @ui.button(label="✅ Accepter", style=discord.ButtonStyle.success)
    async def accept(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in {OWNER_ID, 1140296215469240401}:
            await interaction.response.send_message("❌ Permission denied", ephemeral=True)
            return
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send(f"✅ **Votre suggestion a été acceptée!**\nID: {self.suggestion_id}")
        except:
            pass
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = discord.Color.green()
            embed.title = "✅ Suggestion acceptée"
            await interaction.message.edit(embed=embed, view=None)
        await interaction.response.defer()

    @ui.button(label="❌ Refuser", style=discord.ButtonStyle.danger)
    async def reject(self, interaction: discord.Interaction, button: ui.Button):
        if interaction.user.id not in {OWNER_ID, 1140296215469240401}:
            await interaction.response.send_message("❌ Permission denied", ephemeral=True)
            return
        try:
            user = await bot.fetch_user(self.user_id)
            await user.send(f"❌ **Votre suggestion a été refusée.**\nID: {self.suggestion_id}")
        except:
            pass
        embed = interaction.message.embeds[0] if interaction.message.embeds else None
        if embed:
            embed.color = discord.Color.red()
            embed.title = "❌ Suggestion refusée"
            await interaction.message.edit(embed=embed, view=None)
        await interaction.response.defer()

@tasks.loop(minutes=1)
async def process_site_suggestions():
    """Traite les suggestions du site web"""
    try:
        import os, json
        feedback_file = 'web/data/feedback_queue.json'
        if not os.path.exists(feedback_file):
            return
        
        with open(feedback_file, 'r') as f:
            queue = json.load(f)
        
        channel = bot.get_channel(1344414298847576104)  # Suggestion channel
        if not channel:
            return
        
        processed = []
        for timestamp, data in list(queue.items()):
            if data.get('type') != 'suggestion' or data.get('_sent'):
                continue
            
            try:
                user_id = int(data.get('user_id', 0))
                embed = discord.Embed(
                    title="💡 Nouvelle suggestion",
                    color=discord.Color.gold(),
                    timestamp=discord.utils.utcnow()
                )
                embed.add_field(name="👤 Utilisateur", value=data.get('discord', 'N/A'), inline=False)
                embed.add_field(name="📋 Suggestion", value=data.get('description', 'N/A'), inline=False)
                embed.add_field(name="🏘️ Serveur", value="Site Web", inline=False)
                embed.add_field(name="", value="En attente d'une réponse de l'équipe...", inline=False)
                embed.set_footer(text="Miaouscle Bot")
                
                view = SitesuggestionView(user_id, timestamp)
                msg = await channel.send(embed=embed, view=view)
                
                data['_sent'] = True
                data['message_id'] = msg.id
                processed.append(timestamp)
            except Exception as e:
                print(f"❌ Erreur traitement suggestion: {e}")
        
        # Sauvegarder l'état
        if processed:
            with open(feedback_file, 'w') as f:
                json.dump(queue, f, indent=2)
    except Exception as e:
        print(f"❌ Erreur process_site_suggestions: {e}")


# ============================================================================
# MAIN
# ============================================================================
if __name__ == "__main__":
    TOKEN = os.getenv("DISCORD_TOKEN")
    if not TOKEN:
        print("❌ DISCORD_TOKEN manquant dans .env")
    else:
        bot.run(TOKEN)
