# cogs/moderation.py
"""
Module de modération complètes avec sanctions, automod, et logging.
- Mute / Ban / Kick / Warn / Unmute / Unban
- Automod automatique pour vocabulaire interdit, spam
- Logging complet avec translations multilangues
"""
import discord
from discord.ext import commands
from discord import app_commands, Interaction
from utils.embeds import create_embed
from utils.database import add_warning, get_warnings, load_config
from utils.translations import get_text
from utils.premium import has_premium, TIER_PREMIUM
import asyncio
import json
import os
import logging
import random
import string
from datetime import datetime, timedelta, timezone
from enum import Enum
import re
import unicodedata
from typing import Optional, Union, Tuple, List

logger = logging.getLogger(__name__)

# ============================================================================
# Anti-link & mod log persistence helpers
# ============================================================================
# Détecte une URL http(s)/www ou un domaine nu "exemple.com"
# Nécessite le protocole ou www pour éviter les faux positifs (v13.8.0, fichier.py…)
URL_REGEX = re.compile(
    r"(?:https?://|www\.)"
    r"[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?"
    r"(?:\.[a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?)*"
    r"\.[a-z]{2,}"
    r"(?:/[^\s]*)?",
    re.IGNORECASE,
)
# Domaines nus sans protocole — exige au moins 2 segments avec un TLD lettres-seulement
_BARE_DOMAIN_REGEX = re.compile(
    r"(?<![a-zA-Z0-9/\-_@])"
    r"([a-z0-9](?:[a-z0-9\-]{0,61}[a-z0-9])?\.[a-z]{2,})"
    r"(?:/[^\s]*)?"
    r"(?![a-zA-Z0-9\-])",
    re.IGNORECASE,
)
# Extensions de fichiers et suffixes numériques à ignorer pour éviter les faux positifs
_IGNORE_TLDS = {
    'py', 'js', 'ts', 'json', 'md', 'txt', 'html', 'css', 'yml', 'yaml',
    'png', 'jpg', 'jpeg', 'gif', 'svg', 'mp4', 'mp3', 'pdf', 'zip', 'tar',
    'gz', 'exe', 'dll', 'sh', 'bat', 'csv', 'xml', 'toml', 'cfg', 'log',
    'db', 'sql', 'env', 'lock', 'bak', 'tmp', 'ico', 'webp', 'mov', 'avi',
    'cs', 'go', 'rs', 'rb', 'java', 'php', 'cpp', 'c', 'h',
}

# Domaines toujours autorisés (CDN Discord, Tenor, etc.) — ne JAMAIS sanctionner.
ALWAYS_WHITELIST_DOMAINS = {
    "discord.com", "discord.gg", "discordapp.com", "cdn.discordapp.com",
    "media.discordapp.net", "tenor.com", "media.tenor.com", "giphy.com",
}

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MOD_LOGS_DIR = os.path.join(_PROJECT_ROOT, "web", "data", "mod_logs")
MOD_LOGS_MAX_ENTRIES = 5000  # rotation : on conserve les 5000 plus récents


def _extract_domain(url_match: str) -> str:
    """Renvoie le domaine racine d'une URL ou d'un match (lowercased)."""
    s = url_match.strip().lower()
    s = re.sub(r"^https?://", "", s)
    s = s.split("/", 1)[0].split("?", 1)[0]
    return s


def _domain_in_whitelist(domain: str, whitelist: list) -> bool:
    """Vrai si `domain` (ou un de ses suffixes) est whitelisté."""
    domain = (domain or "").lower()
    if not domain:
        return False
    if domain in ALWAYS_WHITELIST_DOMAINS:
        return True
    if not whitelist:
        return False
    for entry in whitelist:
        if not isinstance(entry, str):
            continue
        e = entry.strip().lower().lstrip(".")
        if not e:
            continue
        if domain == e or domain.endswith("." + e):
            return True
    return False


def _validate_domain(domain: str) -> bool:
    """
    Vérifie qu'un domaine extrait est réellement un nom de domaine valide.
    Exclut : IPs pures, extensions de fichiers, suffixes numériques (v1.2.3).
    """
    if not domain or "." not in domain:
        return False
    parts = domain.split(".")
    tld = parts[-1].lower()
    # TLD doit être uniquement des lettres, longueur 2+
    if not tld.isalpha() or len(tld) < 2:
        return False
    # Exclure les extensions de fichiers / langages connues
    if tld in _IGNORE_TLDS:
        return False
    # Au moins un segment avant le TLD doit contenir des lettres (exclut 192.168.1.1)
    if not any(any(c.isalpha() for c in p) for p in parts[:-1]):
        return False
    return True


def _detect_unauthorized_link(content: str, whitelist: list, block_all: bool = False) -> Optional[str]:
    """Renvoie le 1er domaine NON autorisé trouvé dans `content`, ou None.
    
    Si block_all=True : bloque TOUS les liens sans exception (ignore whitelist
    et ALWAYS_WHITELIST_DOMAINS — même Discord, Tenor, Giphy seront bloqués).
    """
    if not content:
        return None
    # Chercher d'abord les URLs avec protocole ou www (haute fiabilité)
    for match in URL_REGEX.finditer(content):
        url = match.group(0)
        domain = _extract_domain(url)
        if not _validate_domain(domain):
            continue
        if block_all or not _domain_in_whitelist(domain, whitelist):
            return domain
    # Chercher aussi les domaines nus comme "example.com" (sans protocole)
    for match in _BARE_DOMAIN_REGEX.finditer(content):
        url = match.group(0)
        domain = _extract_domain(url)
        if not _validate_domain(domain):
            continue
        if block_all or not _domain_in_whitelist(domain, whitelist):
            return domain
    return None


def _mod_logs_path(guild_id: int) -> str:
    return os.path.join(MOD_LOGS_DIR, f"{guild_id}.json")


def _persist_mod_log_sync(guild_id: int, entry: dict) -> None:
    """Append-only avec rotation. Tolérant aux fichiers corrompus."""
    os.makedirs(MOD_LOGS_DIR, exist_ok=True)
    path = _mod_logs_path(guild_id)
    logs = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and isinstance(data.get("logs"), list):
            logs = data["logs"]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        logs = []
    logs.append(entry)
    if len(logs) > MOD_LOGS_MAX_ENTRIES:
        logs = logs[-MOD_LOGS_MAX_ENTRIES:]
    try:
        with open(path, "w", encoding="utf-8") as f:
            json.dump({"logs": logs}, f, indent=2, ensure_ascii=False)
    except OSError as e:
        logger.error(f"❌ Échec persistance mod-log {guild_id}: {e}")
    # Compteur sanctions du jour pour les Stats avancées (best-effort)
    try:
        from utils.stats_daily import increment_sanctions
        increment_sanctions(guild_id, by=1)
    except Exception as _e:
        logger.debug(f"increment_sanctions échoué (non bloquant) : {_e}")


async def _persist_mod_log(guild_id: int, entry: dict) -> None:
    """Wrapper async (utilise un thread worker pour ne pas bloquer l'event loop)."""
    try:
        await asyncio.to_thread(_persist_mod_log_sync, guild_id, entry)
    except Exception as e:
        logger.debug(f"Erreur persist_mod_log: {e}")


# ============================================================================
# Embed builders — 3 styles visuels distincts
# ============================================================================
_ACTION_CFG: dict = {
    "warn":     {"color": 0xf59e0b, "emoji": "⚠️",  "label": "AVERTISSEMENT",    "label_fr": "Avertissement"},
    "mute":     {"color": 0xf97316, "emoji": "🔇",  "label": "MISE EN SOURDINE", "label_fr": "Mute"},
    "unmute":   {"color": 0x10b981, "emoji": "🔊",  "label": "SOURDINE LEVÉE",   "label_fr": "Démute"},
    "ban":      {"color": 0xef4444, "emoji": "🔨",  "label": "BANNI",            "label_fr": "Ban"},
    "unban":    {"color": 0x10b981, "emoji": "✅",  "label": "DÉBANNI",          "label_fr": "Unban"},
    "kick":     {"color": 0xf97316, "emoji": "👢",  "label": "EXPULSÉ",          "label_fr": "Kick"},
    "auto_mod": {"color": 0x3b82f6, "emoji": "🤖",  "label": "AUTOMOD",          "label_fr": "AutoMod"},
}

_DEBAN_SERVER_ID = 1188506879052238988


def build_log_embed(
    action: str,
    target: Union["discord.Member", "discord.User", str],
    moderator: Union["discord.Member", "discord.User"],
    guild: "discord.Guild",
    *,
    reason: Optional[str] = None,
    duration: Optional[str] = None,
    sanction_id: Optional[str] = None,
    is_automod: bool = False,
    extra: Optional[str] = None,
) -> "discord.Embed":
    """
    Style LOG — posté dans le canal de logs du serveur.
    Visuel : titre préfixé '📋 LOG •', footer avec nom du serveur.
    """
    cfg = _ACTION_CFG.get("auto_mod" if is_automod else action, _ACTION_CFG["warn"])
    embed = discord.Embed(
        title=f"📋 LOG • {cfg['emoji']} {cfg['label']}",
        color=cfg["color"],
        timestamp=datetime.now(timezone.utc),
    )
    if hasattr(target, "mention"):
        target_str = f"{target.mention}\n`{getattr(target, 'name', str(target))}` • ID `{target.id}`"
    else:
        target_str = str(target)
    if hasattr(moderator, "mention"):
        mod_str = f"{moderator.mention}\n`{getattr(moderator, 'name', str(moderator))}`"
    else:
        mod_str = str(moderator)
    embed.add_field(name="👤 Utilisateur", value=target_str, inline=True)
    embed.add_field(name="🛡️ Modérateur", value=mod_str, inline=True)
    if reason:
        embed.add_field(name="📝 Raison", value=f"```{reason[:512]}```", inline=False)
    if duration:
        embed.add_field(name="⏱️ Durée", value=f"**{duration}**", inline=True)
    if sanction_id:
        embed.add_field(name="🆔 ID Sanction", value=f"`{sanction_id}`", inline=True)
    if extra:
        embed.add_field(name="ℹ️ Info", value=extra, inline=False)
    embed.set_footer(
        text=f"📋 Log de Modération • {guild.name}",
        icon_url=guild.icon.url if guild.icon else None,
    )
    return embed


def build_dm_embed(
    action: str,
    guild_name: str,
    *,
    reason: Optional[str] = None,
    duration: Optional[str] = None,
    sanction_id: Optional[str] = None,
    guild_id: Optional[int] = None,
) -> "discord.Embed":
    """
    Style DM — envoyé en message privé à l'utilisateur sanctionné.
    Visuel : titre centré sur le serveur, description courte, thumbnail bot.
    """
    cfg = _ACTION_CFG.get(action, _ACTION_CFG["warn"])
    desc_map = {
        "ban":    "Tu as été **banni** de ce serveur Discord.",
        "kick":   "Tu as été **expulsé** de ce serveur Discord.",
        "mute":   "Tu as été mis en **sourdine** sur ce serveur Discord.",
        "warn":   "Tu as reçu un **avertissement** sur ce serveur Discord.",
        "unmute": "Ta sourdine a été **levée** sur ce serveur Discord.",
        "unban":  "Tu as été **débanni** de ce serveur Discord. Bienvenue de retour !",
    }
    embed = discord.Embed(
        title=f"{cfg['emoji']}  Sanction reçue — {guild_name}",
        description=desc_map.get(action, "Tu as reçu une sanction."),
        color=cfg["color"],
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name="🏠 Serveur", value=guild_name, inline=True)
    embed.add_field(name="📋 Type", value=cfg["label_fr"], inline=True)
    if reason:
        embed.add_field(name="📝 Raison", value=reason[:512], inline=False)
    if duration:
        embed.add_field(name="⏱️ Durée", value=duration, inline=True)
    if sanction_id:
        embed.add_field(name="🆔 ID Sanction", value=f"`{sanction_id}`", inline=True)
    if action == "ban" and guild_id == _DEBAN_SERVER_ID:
        embed.add_field(name="\u200b", value="[🔗 Rejoins le serveur de déban](https://discord.gg/mq3pYpfxeV)", inline=False)
        embed.add_field(name="\u200b", value="*Tu peux demander un débannissement.*", inline=False)
    embed.set_thumbnail(url="https://cdn.discordapp.com/embed/avatars/0.png")
    embed.set_footer(text="⚖️ Miaouscle Modération • Contacte le staff si tu penses que c'est une erreur.")
    return embed


def build_confirm_embed(
    action: str,
    target: Union["discord.Member", "discord.User", int],
    *,
    reason: Optional[str] = None,
    duration: Optional[str] = None,
    sanction_id: Optional[str] = None,
    dm_sent: bool = True,
) -> "discord.Embed":
    """
    Style CONFIRMATION — réponse éphémère au modérateur.
    Visuel : toujours vert (succès), compact, montre statut DM.
    """
    cfg = _ACTION_CFG.get(action, _ACTION_CFG["warn"])
    if hasattr(target, "mention"):
        target_str = f"{target.mention}  `{getattr(target, 'name', str(target))}`"
    else:
        target_str = f"`{target}`"
    embed = discord.Embed(
        title=f"✅ {cfg['label_fr']} appliqué",
        color=0x22c55e,
        timestamp=datetime.now(timezone.utc),
    )
    embed.add_field(name=f"{cfg['emoji']} Action", value=cfg["label_fr"], inline=True)
    embed.add_field(name="👤 Cible", value=target_str, inline=True)
    if reason:
        embed.add_field(name="📝 Raison", value=f"```{reason[:256]}```", inline=False)
    if duration:
        embed.add_field(name="⏱️ Durée", value=f"**{duration}**", inline=True)
    if sanction_id:
        embed.add_field(name="🆔 ID Sanction", value=f"`{sanction_id}`", inline=True)
    dm_status = "✅ DM envoyé" if dm_sent else "❌ DMs désactivés"
    embed.add_field(name="📨 Notification", value=dm_status, inline=True)
    embed.set_footer(text="Miaouscle Modération")
    return embed


# ============================================================================
# SanctionListeView — pagination avec boutons Précédent / Suivant
# ============================================================================
class SanctionListeView(discord.ui.View):
    """View avec pagination pour /sanction-liste."""

    PER_PAGE = 5
    _TYPE_EMOJI = {
        "warn": "⚠️", "ban": "🔨", "kick": "👢",
        "mute": "🔇", "unmute": "🔊", "unban": "✅", "auto_mod": "🤖",
    }
    _TYPE_LABEL = {
        "warn": "Avertissement", "ban": "Ban", "kick": "Kick",
        "mute": "Mute", "unmute": "Démute", "unban": "Unban", "auto_mod": "AutoMod",
    }

    def __init__(
        self,
        warnings: list,
        membre: "discord.Member",
    ) -> None:
        super().__init__(timeout=120)
        self.warnings = list(reversed(warnings))  # du plus récent au plus ancien
        self.membre = membre
        self.page = 0
        self.total_pages = max(1, (len(self.warnings) + self.PER_PAGE - 1) // self.PER_PAGE)
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.prev_btn.disabled = self.page == 0
        self.next_btn.disabled = self.page >= self.total_pages - 1

    def build_embed(self) -> "discord.Embed":
        start = self.page * self.PER_PAGE
        chunk = self.warnings[start: start + self.PER_PAGE]
        embed = discord.Embed(
            title=f"📋 Sanctions — {self.membre.display_name}  (`{self.membre.name}`)",
            description=f"**{len(self.warnings)}** sanction(s) enregistrée(s)",
            color=0xff9900,
            timestamp=datetime.now(timezone.utc),
        )
        if self.membre.display_avatar:
            embed.set_thumbnail(url=self.membre.display_avatar.url)
        for w in chunk:
            s_type = w.get("type", "warn")
            s_reason = w.get("reason", "Pas de raison")
            s_id = w.get("sanction_id") or w.get("id", "-")
            mod_id = w.get("moderator_id")
            mod_name = w.get("moderator_name", "?")
            mod_str = f"<@{mod_id}>" if mod_id else f"`{mod_name}`"
            ts_raw = w.get("timestamp", "")
            ts_fmt = "Date inconnue"
            if ts_raw:
                try:
                    ts_fmt = datetime.fromisoformat(ts_raw).strftime("%d/%m/%Y %H:%M")
                except Exception:
                    ts_fmt = str(ts_raw)
            emoji = self._TYPE_EMOJI.get(s_type, "📌")
            label = self._TYPE_LABEL.get(s_type, s_type.title())
            field_name = f"{emoji} {label}  —  `{s_id}`"
            field_value = (
                f"**Raison :** {s_reason[:200]}\n"
                f"**Par :** {mod_str}  •  **Le :** {ts_fmt}"
            )
            embed.add_field(name=field_name, value=field_value, inline=False)
        embed.set_footer(text=f"Page {self.page + 1}/{self.total_pages}  •  ID : {self.membre.id}")
        return embed

    @discord.ui.button(label="◀ Précédent", style=discord.ButtonStyle.secondary)
    async def prev_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = max(0, self.page - 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    @discord.ui.button(label="Suivant ▶", style=discord.ButtonStyle.secondary)
    async def next_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.page = min(self.total_pages - 1, self.page + 1)
        self._refresh_buttons()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def on_timeout(self) -> None:
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]


# Configuration
OWNER_ID = 653038666625785866
PRIORITY_IDS = {OWNER_ID, 1140296215469240401}

# ============================================================================
# Constants & Enums
# ============================================================================
class SanctionType(Enum):
    """Types de sanctions disponibles"""
    WARN = "warn"
    BAN = "ban"
    UNBAN = "unban"
    KICK = "kick"
    MUTE = "mute"
    UNMUTE = "unmute"
    AUTO_MOD = "auto_mod"


class AutomodViolationType(Enum):
    """Types de violations Automod"""
    FORBIDDEN_WORDS = "forbidden_words"
    UPPERCASE_SPAM = "uppercase_spam"
    MENTION_SPAM = "mention_spam"
    URL_LINK = "url_link"


# Vocabulaire interdit (insultes courantes et variantes multilingues).
# Les termes très courts restent entourés de frontières de mots dans
# `censor_forbidden_words` afin d'éviter les faux positifs dans les mots
# normaux qui contiennent les mêmes lettres.
FORBIDDEN_WORDS = {
    # Français
    "putain", "pute", "salope", "connard", "con", "conne", "merde", "chieur",
    "encule", "enculee", "débile", "handicapé", "handicapée", "péde",
    "tapette", "salaud", "salopard", "estropie", "pimbêche", "tarte", "connasse",
    "fdp", "bâtard", "racaille", "gueule", "ferme-la",
    # Abréviations et argot de chat (français / international)
    "slp", "ptn", "ntm", "nsm", "vtff", "vtd", "ftg", "keh", "enc",
    "filsdepute", "hdf", "hdp", "ptm", "ctm",
    # Anglais
    "fuck", "shit", "ass", "bitch", "bastard", "asshole", "damn", "hell",
    "crap", "piss", "whore", "slut", "douchebag", "dickhead",
    "motherfucker", "bullshit", "dumbass", "sonofabitch", "fck", "fuk",
    "sh1t", "btch", "stfu", "gtfo", "wtf", "omfg",
    # Espagnol
    "puta", "puto", "mierda", "joder", "coño", "cabron", "cabrón",
    "pendejo", "gilipollas", "maricon", "maricón", "zorra", "culero",
    "verga", "chingada", "chingar",
    # Italien
    "cazzo", "merda", "stronzo", "stronza", "puttana", "troia",
    "bastardo", "vaffanculo", "coglione",
    # Portugais
    "caralho", "puta", "puto", "buceta", "viado", "otario", "otário",
    "foda", "fodase", "cuzao", "cuzão",
    # Néerlandais
    "kut", "klootzak", "tering", "tyfus", "godverdomme", "hoer", "lul",
    "eikel", "sukkel", "neuken",
    # Allemand
    "scheisse", "scheiße", "fick", "arschloch", "hurensohn", "wichser",
    "fotze", "schlampe", "mistkerl", "verpiss",
    # Polonais
    "kurwa", "chuj", "huja", "pizda", "jebac", "jebać", "skurwysyn",
    "spierdalaj", "suka",
    # Russe (alphabet cyrillique et translittérations courantes)
    "блядь", "сука", "ебать", "пиздец", "хуй", "мудак",
    "blyat", "bljat", "pizdec", "ebat", "yebat", "khuy", "mudak",
    # Turc
    "amk", "aq", "siktir", "orospu", "piç", "yarrak", "salak",
    "gerizekalı",
    # Arabe (termes courants et translittérations)
    "شرموطة", "قحبة", "خرا", "كس", "sharmouta", "kahba", "khara",
    "zebbi", "zebi",
    # Japonais
    "くそ", "クソ", "ばか", "バカ", "死ね", "しね", "ちくしょう",
    # Coréen
    "씨발", "시발", "개새끼", "병신", "좆", "꺼져",
    # Chinois
    "妈的", "媽的", "傻逼", "傻B", "他妈的", "他媽的", "滚", "滾",
    # Grec (translittération)
    "malaka", "malakas", "gamoto", "poutana", "skata",
    # Hindi / ourdou (translittérations courantes)
    "chutiya", "madarchod", "bhenchod", "gandu", "randi", "harami",
    # Indonésien
    "anjing", "bangsat", "kontol", "memek", "jancuk", "brengsek",
    # Scandinave
    "helvete", "fitta", "hora", "jævel", "javel", "luder", "fisse",
}

# Expressions régulières interdites
FORBIDDEN_PHRASES = [
    r"\btg\b",
    r"\b(?:slp|ptn|ntm|nsm|vtff|vtd|ftg|keh|fck|fuk|sh1t|btch|stfu|gtfo|wtf|omfg)\b",
    r"\bta[ -]?gueule\b",
    r"\bferme[ -]?(?:ta[ -]?)?gueule\b",
    r"\bferme[ -]?la\b",
    r"\bva[ -]?te[ -]?faire[ -]?(?:foutre|enculer)\b",
    r"\bhijo[ -]?de[ -]?puta\b",
    r"\bfiglio[ -]?di[ -]?puttana\b",
    r"\bfilho[ -]?da[ -]?puta\b",
    r"\bson[ -]?of[ -]?a[ -]?bitch\b",
    r"\bmother[ -]?fucker\b",
    r"\bverpiss[ -]?dich\b",
    r"\bhalt[ -]?die[ -]?fresse\b",
    r"\bspierdalaj\b",
    r"\b(?:kos[ -]?omak|ibn[ -]?el[ -]?kahba)\b",
    r"\b(?:pute|putain|salope|pimbêche)\b",
    r"\bconnard\b",
    r"\bcona?rd\b",
    r"\bencul[ée]?\b",
    r"\b(?:merde|chiant|chieur)\b",
    r"\bestrop[ié]?e?\b",
    r"\b(?:débile|handicapé|handicapée)\b",
    r"\b(?:pédé|tapette)\b",
    r"\b(?:con|conne)\b(?!.*qu)",
    r"\b(?:salaud|salopard)\b",
    r"\b(?:pauvre type|débile profond)\b",
]

# Valeurs par défaut pour Automod
DEFAULT_AUTOMOD_SETTINGS = {
    "enabled": True,
    "action": "warn",  # warn, mute, kick, ban
    "duration_minutes": 10,
}

# ============================================================================
# Utility Functions
# ============================================================================
def generate_sanction_id(sanction_type: Optional[SanctionType] = None) -> str:
    """
    Génère un ID intelligent pour une sanction au format: {TYPE}-{DATE}-{CODE}
    Exemple: W-251130-A7X2K (Warn le 25/11/2030 avec code unique de 5 caractères)

    Args:
        sanction_type: Type de sanction (pour le préfixe)

    Returns:
        ID formaté du type: W-251130-A7X2K
    """
    # Mapping des préfixes pour chaque type
    type_prefixes = {
        SanctionType.WARN: "W",
        SanctionType.BAN: "B",
        SanctionType.UNBAN: "UB",
        SanctionType.KICK: "K",
        SanctionType.MUTE: "M",
        SanctionType.UNMUTE: "UM",
        SanctionType.AUTO_MOD: "AM",
    }

    # Obtenir le préfixe selon le type
    prefix = type_prefixes.get(sanction_type, "SN") if sanction_type else "SN"

    # Date au format DDMMYY (exemple: 251130 = 25/11/30)
    now = datetime.now(timezone.utc)
    date_str = now.strftime("%d%m%y")

    # Code aléatoire unique - EXACTEMENT 5 caractères min/max
    code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=5))

    # Format final: W-251130-A7X2K (avec code de EXACTEMENT 5 caractères)
    return f"{prefix}-{date_str}-{code}"


def format_duration(minutes: Optional[int]) -> Optional[str]:
    """Formatte une durée en minutes de manière lisible."""
    if minutes is None:
        return None
    if minutes < 60:
        return f"{minutes}m"
    hours = minutes // 60
    mins = minutes % 60
    return f"{hours}h {mins}m" if mins else f"{hours}h"


def censor_forbidden_words(content: str) -> Tuple[str, bool]:
    """
    Remplace les gros mots interdits par des *** et retourne le texte censuré.

    Returns:
        tuple: (texte_censuré, a_des_gros_mots)
    """
    censored = content
    found_forbidden = False
    normalized_content = unicodedata.normalize("NFKC", content).casefold()

    # Utiliser des frontières de caractères plutôt que split()/strip() :
    # cela détecte aussi « fuck! », « pute… », « (fuck) » et les variantes
    # avec ponctuation Unicode, sans faire de recherche par sous-chaîne.
    for forbidden_word in FORBIDDEN_WORDS:
        normalized_word = unicodedata.normalize("NFKC", forbidden_word).casefold()
        if re.search(r"(?<!\w)" + re.escape(normalized_word) + r"(?!\w)", normalized_content):
            found_forbidden = True
            censored = re.sub(
                r"(?<!\w)" + re.escape(forbidden_word) + r"(?!\w)",
                lambda match: "*" * len(match.group(0)),
                censored,
                flags=re.IGNORECASE,
            )

    # Vérifier les expressions interdites
    for pattern in FORBIDDEN_PHRASES:
        if re.search(pattern, content, re.IGNORECASE):
            found_forbidden = True
            censored = re.sub(pattern, lambda m: '*' * len(m.group(0)), censored, flags=re.IGNORECASE)

    return censored, found_forbidden


# ============================================================================
# Moderation Cog
# ============================================================================
class Moderation(commands.Cog):
    """Cog de modération: mute/ban/kick/warn/unmute/unban/automod."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._config_cache = {}  # Cache pour les configurations

    # ========================================================================
    # Helper Methods - Configuration & Cache
    # ========================================================================
    async def _get_config(self, guild_id: int) -> dict:
        """Récupère la configuration du serveur avec cache."""
        if guild_id not in self._config_cache:
            self._config_cache[guild_id] = load_config(guild_id) or {}
        return self._config_cache[guild_id]

    def _invalidate_config_cache(self, guild_id: int) -> None:
        """Invalide le cache de configuration d'un serveur."""
        if guild_id in self._config_cache:
            del self._config_cache[guild_id]

    def _is_automod_exempt(self, member: discord.Member, guild: discord.Guild) -> bool:
        """
        Retourne True si le membre est protégé contre l'automod.
        Respecte la hiérarchie Discord complète :
        - Propriétaire du serveur
        - Administrateur
        - Permissions de modération (gérer messages, kick, ban, gérer serveur)
        - Rôle supérieur ou égal au bot (le bot ne peut pas agir sur eux)
        - IDs prioritaires (owner du bot, etc.)
        """
        if member.id in PRIORITY_IDS:
            return True
        if guild.owner_id == member.id:
            return True
        perms = member.guild_permissions
        if perms.administrator:
            return True
        if perms.manage_guild or perms.manage_messages or perms.kick_members or perms.ban_members:
            return True
        # Vérifier si le bot peut réellement agir sur ce membre (hiérarchie des rôles)
        bot_member = guild.me
        if bot_member and member.top_role >= bot_member.top_role:
            return True
        return False

    async def _resolve_log_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Résout le canal de logs configuré pour le serveur."""
        try:
            config = await self._get_config(guild.id)
            channel_id = config.get("log_channel")
            if not channel_id:
                return None

            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return None

            channel = self.bot.get_channel(channel_id) or guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        except Exception as e:
            logger.debug(f"Erreur lors de la résolution du canal de logs: {e}")

        return None

    async def _resolve_automod_channel(self, guild: discord.Guild) -> Optional[discord.TextChannel]:
        """Résout le canal d'automod configuré pour le serveur.
        Essaie d'abord automod_channel, puis log_channel en fallback."""
        try:
            config = await self._get_config(guild.id)
            # Priorité : canal automod dédié, sinon canal de logs général
            channel_id = config.get("automod_channel") or config.get("log_channel")
            if not channel_id:
                return None

            try:
                channel_id = int(channel_id)
            except (TypeError, ValueError):
                return None

            channel = self.bot.get_channel(channel_id) or guild.get_channel(channel_id)
            if isinstance(channel, discord.TextChannel):
                return channel
        except Exception as e:
            logger.debug(f"Erreur lors de la résolution du canal automod: {e}")

        return None

    # ========================================================================
    # Helper Methods - Notifications & Logging
    # ========================================================================
    async def _send_dm(
        self,
        user: Union[discord.User, discord.Member, int],
        title: str,
        fields: Optional[List[Tuple[str, str]]] = None,
        footer: Optional[str] = None,
        action_type: Optional[SanctionType] = None,
        guild_id: Optional[int] = None,
        guild_name: Optional[str] = None,
    ) -> bool:
        """Envoie un DM avec le style DM distinctif (build_dm_embed)."""
        try:
            if isinstance(user, int):
                try:
                    user = await self.bot.fetch_user(user)
                    logger.info(f"✅ User fetch réussi pour ID {user.id}")
                except Exception as e:
                    logger.info(f"Impossible de fetch l'utilisateur {user}: {e}")
                    return False

            action_str = action_type.value if action_type else "warn"
            # Extraire raison et sanction_id depuis les fields legacy
            reason = None
            duration = None
            sanction_id = None
            server_name = guild_name or "Serveur Discord"
            if fields:
                for name, value in fields:
                    nl = name.lower()
                    if any(k in nl for k in ("raison", "reason")):
                        reason = value
                    elif any(k in nl for k in ("durée", "duration", "duree")):
                        duration = value
                    elif any(k in nl for k in ("sanction", "id sanction")):
                        sanction_id = value
                    elif any(k in nl for k in ("serveur", "server")):
                        server_name = value

            embed = build_dm_embed(
                action_str,
                server_name,
                reason=reason,
                duration=duration,
                sanction_id=sanction_id,
                guild_id=guild_id,
            )
            logger.info(f"📨 Tentative d'envoi DM à {user.id}")
            await user.send(embed=embed)
            logger.info(f"✅ DM envoyé à {user.id}")
            return True
        except discord.HTTPException as e:
            logger.error(f"❌ Erreur HTTP DM pour {user}: {e}")
            return False
        except discord.ClientException as e:
            logger.error(f"❌ Erreur Client DM pour {user}: {e}")
            return False
        except Exception as e:
            logger.error(f"❌ Erreur générale DM pour {user}: {type(e).__name__}: {e}")
            return False

    async def _send_mod_log(
        self,
        guild: discord.Guild,
        action: str,
        moderator: Union[discord.Member, discord.User],
        target: Union[discord.Member, discord.User, str],
        reason: Optional[str] = None,
        duration: Optional[str] = None,
        sanction_id: Optional[str] = None,
        is_automod: bool = False,
    ) -> None:
        """Envoie un log détaillé (style LOG distinctif) + persistance JSON."""
        # Persistance dashboard (toujours, même sans canal de log configuré)
        try:
            target_id = getattr(target, "id", None)
            target_name = (
                target.name if isinstance(target, (discord.Member, discord.User))
                else str(target)
            )
            mod_id = getattr(moderator, "id", None)
            mod_name = getattr(moderator, "name", str(moderator))
            await _persist_mod_log(guild.id, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "automod" if is_automod else "mod",
                "action": action,
                "user_id": target_id,
                "user_name": target_name,
                "moderator_id": mod_id,
                "moderator_name": mod_name,
                "reason": reason,
                "duration": duration,
                "sanction_id": sanction_id,
            })
        except Exception as e:
            logger.debug(f"Erreur persist mod log: {e}")

        try:
            channel = await self._resolve_log_channel(guild)
            if not channel:
                return
            embed = build_log_embed(
                action,
                target,
                moderator,
                guild,
                reason=reason,
                duration=duration,
                sanction_id=sanction_id,
                is_automod=is_automod,
            )
            await channel.send(embed=embed)
        except Exception as e:
            logger.debug(f"Erreur log: {e}")

    async def _send_automod_log(
        self,
        guild: Optional[discord.Guild],
        violation_type: AutomodViolationType,
        user: Union[discord.Member, discord.User],
        channel: Union[discord.TextChannel, discord.VoiceChannel],
        reason: Optional[str],
        censored_content: str,
    ) -> None:
        """Envoie un log d'automod dans le canal configuré + persistance JSON."""
        if not guild:
            return

        # Persistance dashboard (toujours)
        try:
            await _persist_mod_log(guild.id, {
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "automod",
                "action": violation_type.value,
                "user_id": getattr(user, "id", None),
                "user_name": getattr(user, "name", str(user)),
                "channel_id": getattr(channel, "id", None),
                "channel_name": getattr(channel, "name", None),
                "reason": reason,
                "content": censored_content,
            })
        except Exception as e:
            logger.debug(f"Erreur persist automod log: {e}")

        try:
            automod_channel = await self._resolve_automod_channel(guild)
            if not automod_channel:
                return

            embed = discord.Embed(
                title=get_text(guild.id, "moderation.automod_title"),
                color=0xff6600,
                timestamp=datetime.now(timezone.utc),
            )

            embed.add_field(
                name=get_text(guild.id, "moderation.automod_user"),
                value=f"{user.mention} ({user})",
                inline=False,
            )
            embed.add_field(
                name=get_text(guild.id, "moderation.automod_type"),
                value=violation_type.value.upper(),
                inline=True,
            )
            embed.add_field(
                name=get_text(guild.id, "moderation.reason"),
                value=reason,
                inline=True,
            )
            embed.add_field(
                name=get_text(guild.id, "moderation.channel"),
                value=channel.mention,
                inline=True,
            )
            embed.add_field(
                name=get_text(guild.id, "moderation.message"),
                value=f"```{censored_content[:200]}```",
                inline=False,
            )
            embed.set_footer(text=f"User ID: {user.id}")

            await automod_channel.send(embed=embed)
        except Exception as e:
            logger.debug(f"Erreur lors de l'envoi du log automod: {e}")

    # ========================================================================
    # Helper Methods - Sanction Application
    # ========================================================================
    async def _apply_sanction(
        self,
        interaction: discord.Interaction,
        member: Union[discord.Member, discord.User, int],
        reason: str,
        action_type: SanctionType,
        minutes: Optional[int] = None,
    ) -> None:
        """
        Applique une sanction et effectue tous les logs/notifications.
        """
        sanction_id = generate_sanction_id(action_type)
        duration_text = format_duration(minutes) if minutes else None
        guild_id = interaction.guild.id if interaction.guild else 0

        # Defer si pas encore répondu
        try:
            await interaction.response.defer(thinking=True)
        except Exception:
            pass

        try:
            guild = interaction.guild
            actor = interaction.user

            # Validation des permissions
            if guild and isinstance(actor, discord.Member):
                required_perms = {
                    SanctionType.BAN: "ban_members",
                    SanctionType.KICK: "kick_members",
                    SanctionType.MUTE: "manage_messages",
                    SanctionType.UNMUTE: "manage_messages",
                    SanctionType.UNBAN: "ban_members",
                    SanctionType.WARN: "manage_messages",
                }

                perm = required_perms.get(action_type)
                if perm:
                    has_perm = (
                        getattr(actor.guild_permissions, perm, False)
                        or actor.id in PRIORITY_IDS
                    )
                    if not has_perm:
                        await interaction.followup.send(
                            embed=create_embed(
                                get_text(guild_id, "moderation.permission_error"),
                                get_text(guild_id, "moderation.permission_error_desc"),
                                0xff0000,
                            ),
                            ephemeral=True,
                        )
                        return

            # Initialiser member_id au début
            member_id = None
            if isinstance(member, int):
                member_id = member
            elif member and hasattr(member, "id"):
                member_id = member.id

            # POUR BAN: Vérifier la possibilité AVANT d'envoyer DM
            if guild and member_id and action_type == SanctionType.BAN:
                already_banned = False
                try:
                    await guild.fetch_ban(discord.Object(member_id))
                    already_banned = True
                except discord.NotFound:
                    already_banned = False
                except Exception:
                    already_banned = False

                if already_banned:
                    await interaction.followup.send(
                        embed=create_embed(
                            "❌ Utilisateur Déjà Banni",
                            f"L'utilisateur est déjà banni du serveur.",
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

                # Vérifier si on peut vraiment bannir
                can_ban = False
                try:
                    if isinstance(member, discord.Member):
                        can_ban = guild.me.top_role > member.top_role
                    else:
                        can_ban = True  # Si pas de member, on peut essayer
                except Exception:
                    can_ban = False

                if not can_ban:
                    await interaction.followup.send(
                        embed=create_embed(
                            "❌ Impossible de Bannir",
                            f"Le bot n'a pas les permissions pour bannir cet utilisateur.",
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

            # ENVOYER LE DM AVANT L'ACTION - pour BAN/KICK/MUTE/WARN
            dm_sent = False
            if guild and member_id and action_type in [SanctionType.BAN, SanctionType.KICK, SanctionType.MUTE, SanctionType.WARN]:
                user_to_dm = None
                try:
                    if isinstance(member, (discord.User, discord.Member)):
                        user_to_dm = member
                    else:
                        user_to_dm = await self.bot.fetch_user(member_id)
                except Exception as e:
                    logger.info(f"Impossible de fetch user {member_id}: {e}")
                    user_to_dm = None

                if user_to_dm:
                    dm_fields = [(get_text(guild_id, "common.server"), guild.name)]
                    if reason:
                        dm_fields.append((get_text(guild_id, "moderation.reason"), reason))
                    if action_type == SanctionType.MUTE and duration_text:
                        dm_fields.append((get_text(guild_id, "moderation.duration"), duration_text))
                    dm_fields.append((get_text(guild_id, "moderation.sanction_id"), sanction_id))

                    dm_sent = await self._send_dm(
                        user_to_dm,
                        "",
                        dm_fields,
                        None,
                        action_type=action_type,
                        guild_id=guild.id,
                        guild_name=guild.name,
                    )
                    if dm_sent:
                        logger.info(f"✅ DM ENVOYÉ AVANT {action_type.value} à {member_id}")
                    else:
                        logger.warning(f"⚠️ Impossible d'envoyer DM AVANT {action_type.value} à {member_id}")

            # Application de l'action
            if action_type == SanctionType.MUTE:
                if minutes is None:
                    await interaction.followup.send(
                        embed=create_embed(
                            get_text(guild_id, "moderation.mute.missing_duration"),
                            get_text(guild_id, "moderation.mute.missing_duration_desc"),
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

                if isinstance(member, discord.Member):
                    # Vérification hiérarchie
                    if guild and guild.owner_id == member.id:
                        await interaction.followup.send(
                            embed=create_embed("❌ Impossible de Muter", "Vous ne pouvez pas muter le propriétaire du serveur.", 0xff0000),
                            ephemeral=True,
                        )
                        return
                    if guild and guild.me and member.top_role >= guild.me.top_role:
                        await interaction.followup.send(
                            embed=create_embed("❌ Impossible de Muter", "Ce membre a un rôle supérieur ou égal au bot.", 0xff0000),
                            ephemeral=True,
                        )
                        return
                    until = datetime.now(timezone.utc) + timedelta(minutes=minutes)
                    await member.timeout(until, reason=f"{actor}: {reason}")
                    self.bot.loop.create_task(self._notify_timeout_end(member, minutes, sanction_id))
                else:
                    await interaction.followup.send(
                        embed=create_embed(
                            get_text(guild_id, "moderation.mute.member_only_error"),
                            get_text(guild_id, "moderation.mute.member_only_error_desc"),
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

            elif action_type == SanctionType.BAN:
                # Appliquer le ban (vérifications déjà faites plus haut)
                if isinstance(member, discord.Member):
                    await member.ban(reason=f"{actor}: {reason}")
                elif isinstance(member, discord.User) and guild:
                    await guild.ban(member, reason=f"{actor}: {reason}")
                elif isinstance(member, int) and guild:
                    user = await self.bot.fetch_user(member)
                    await guild.ban(user, reason=f"{actor}: {reason}")

            elif action_type == SanctionType.KICK:
                if isinstance(member, discord.Member):
                    # Vérification hiérarchie
                    if guild and guild.owner_id == member.id:
                        await interaction.followup.send(
                            embed=create_embed("❌ Impossible de Kick", "Vous ne pouvez pas expulser le propriétaire du serveur.", 0xff0000),
                            ephemeral=True,
                        )
                        return
                    if guild and guild.me and member.top_role >= guild.me.top_role:
                        await interaction.followup.send(
                            embed=create_embed("❌ Impossible de Kick", "Ce membre a un rôle supérieur ou égal au bot.", 0xff0000),
                            ephemeral=True,
                        )
                        return
                    await member.kick(reason=f"{actor}: {reason}")
                else:
                    await interaction.followup.send(
                        embed=create_embed(
                            get_text(guild_id, "moderation.kick.member_only_error"),
                            "",
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

            elif action_type == SanctionType.UNMUTE:
                if isinstance(member, discord.Member):
                    await member.timeout(None, reason=f"{actor}: {reason}")
                else:
                    await interaction.followup.send(
                        embed=create_embed(
                            get_text(guild_id, "moderation.unmute.member_only_error"),
                            get_text(guild_id, "moderation.unmute.member_only_error_desc"),
                            0xff0000,
                        ),
                        ephemeral=True,
                    )
                    return

            elif action_type == SanctionType.UNBAN:
                if guild:
                    try:
                        # Si member est un int (ID), l'utiliser directement
                        if isinstance(member, int):
                            user = await self.bot.fetch_user(member)
                            await guild.unban(user, reason=f"{actor}: {reason}")
                        else:
                            await guild.unban(member, reason=f"{actor}: {reason}")
                    except discord.NotFound:
                        await interaction.followup.send(
                            embed=create_embed(
                                "❌ Utilisateur Non Banni",
                                f"L'utilisateur n'est pas banni du serveur.",
                                0xff0000,
                            ),
                            ephemeral=True,
                        )
                        return
                    except Exception as e:
                        logger.debug(f"Erreur débannissement: {e}")
                        raise

            elif action_type == SanctionType.WARN:
                pass  # Aucune action Discord, juste enregistrer

            # Enregistrement en base de données
            try:
                if guild and member_id:
                    add_warning(
                        guild.id,
                        member_id,
                        actor.id,
                        f"{reason} ({duration_text})" if duration_text else reason,
                        action_type.value,
                        sanction_id=sanction_id,
                    )
            except Exception as e:
                logger.exception("Erreur lors de l'enregistrement de la sanction")

            # DM APRÈS L'ACTION pour UNMUTE/UNBAN uniquement (bonne nouvelle → DM après)
            if guild and member_id and action_type in [SanctionType.UNMUTE, SanctionType.UNBAN]:
                user_to_dm = None
                try:
                    if isinstance(member, (discord.User, discord.Member)):
                        user_to_dm = member
                    else:
                        user_to_dm = await self.bot.fetch_user(member_id)
                except Exception as e:
                    logger.info(f"Impossible de fetch user {member_id}: {e}")
                    user_to_dm = None

                if user_to_dm:
                    dm_fields = [(get_text(guild_id, "common.server"), guild.name)]
                    if reason:
                        dm_fields.append((get_text(guild_id, "moderation.reason"), reason))
                    dm_fields.append((get_text(guild_id, "moderation.sanction_id"), sanction_id))

                    dm_sent = await self._send_dm(
                        user_to_dm,
                        "",
                        dm_fields,
                        None,
                        action_type=action_type,
                        guild_id=guild_id,
                        guild_name=guild.name,
                    )
                    if dm_sent:
                        logger.info(f"✅ DM APRÈS {action_type.value} à {member_id}")
                    else:
                        logger.warning(f"⚠️ Impossible d'envoyer DM APRÈS {action_type.value} à {member_id}")

            # Log de modération pour TOUTES les actions (canal de logs + persistance JSON)
            if guild:
                log_target = member if not isinstance(member, int) else str(member_id)
                await self._send_mod_log(
                    guild,
                    action_type.value,
                    actor,
                    log_target,
                    reason=reason,
                    duration=duration_text,
                    sanction_id=sanction_id,
                )

            # Confirmation — style CONFIRMATION (toujours vert, compact)
            confirm_embed = build_confirm_embed(
                action_type.value,
                member,
                reason=reason,
                duration=duration_text,
                sanction_id=sanction_id,
                dm_sent=dm_sent,
            )
            await interaction.followup.send(embed=confirm_embed)

        except discord.Forbidden:
            await interaction.followup.send(
                embed=create_embed(
                    get_text(guild_id, "moderation.bot_permissions_error"),
                    get_text(guild_id, "moderation.bot_permissions_error_desc"),
                    0xff0000,
                ),
                ephemeral=True,
            )
        except Exception as e:
            logger.exception(f"Erreur lors de l'application de la sanction: {e}")
            error_embed = create_embed(
                get_text(guild_id, "moderation.apply_error"),
                get_text(guild_id, "moderation.apply_error_desc", error=str(e)),
                0xff0000,
            )
            await interaction.followup.send(embed=error_embed, ephemeral=True)

    async def _notify_timeout_end(self, member: Union[discord.Member, discord.User], minutes: int, sanction_id: str) -> None:
        """Notifie l'utilisateur que son mute est terminé."""
        if not minutes:
            return

        try:
            await asyncio.sleep(minutes * 60)
            await self._send_dm(
                member,
                get_text(0, "moderation.timeout_end_title"),
                [
                    (get_text(0, "moderation.reason"), get_text(0, "moderation.timeout_end_desc", duration=format_duration(minutes))),
                    (get_text(0, "moderation.sanction_id"), sanction_id),
                ],
                get_text(0, "moderation.timeout_end_footer"),
            )
        except Exception as e:
            logger.debug(f"Erreur notification fin de timeout: {e}")

    def _get_sanction_title(self, guild_id: int, sanction_type: SanctionType, guild_name: Optional[str] = None) -> str:
        """Retourne le titre formaté pour une sanction."""
        titles = {
            SanctionType.WARN: get_text(guild_id, "moderation.warn.title"),
            SanctionType.BAN: get_text(guild_id, "moderation.ban.title"),
            SanctionType.UNBAN: get_text(guild_id, "moderation.unban.title", default="📋 Débannissement"),
            SanctionType.KICK: get_text(guild_id, "moderation.kick.title"),
            SanctionType.MUTE: get_text(guild_id, "moderation.mute.title"),
            SanctionType.UNMUTE: get_text(guild_id, "moderation.unmute.title"),
        }
        # Pour le BAN, afficher juste le nom du serveur
        if sanction_type == SanctionType.BAN and guild_name:
            return f"Ban du serveur ({guild_name})"

        title = titles.get(sanction_type, sanction_type.value.title())
        return title

    # ========================================================================
    # Event Listeners - Automod
    # ========================================================================
    @commands.Cog.listener()
    async def on_message(self, message: discord.Message) -> None:
        """Automod: détecte et répond aux violations."""
        # Ignorer les bots et les messages hors serveur
        if message.author.bot or not isinstance(message.channel, discord.TextChannel) or not message.guild:
            return

        # L'AutoMod doit voir immédiatement les changements du dashboard.
        # Les commandes peuvent utiliser le cache, mais les messages arrivent
        # dans un processus séparé du serveur Flask : on relit donc la source
        # persistante à chaque message.
        config = load_config(message.guild.id) or {}
        if not config.get("automod_enabled", True):
            return

        content = message.content or ""
        violation_type = None
        violation_reason = None
        censored_content = content

        # Vérifier le vocabulaire interdit
        censored_content, has_forbidden = censor_forbidden_words(content)
        if has_forbidden:
            violation_type = AutomodViolationType.FORBIDDEN_WORDS
            violation_reason: Optional[str] = get_text(message.guild.id, "moderation.reason", default="Vocabulaire interdit")

        # Vérifier les majuscules excessives
        if not violation_type and len(content) > 10:
            uppercase_ratio = sum(1 for c in content if c.isupper()) / len(content)
            if uppercase_ratio > 0.7:
                violation_type = AutomodViolationType.UPPERCASE_SPAM
                violation_reason = get_text(message.guild.id, "moderation.reason", default="Utilisation excessive de majuscules")

        # Vérifier les mentions excessives
        if not violation_type and len(message.mentions) > 5:
            violation_type = AutomodViolationType.MENTION_SPAM
            violation_reason = get_text(message.guild.id, "moderation.reason", default="Trop de mentions")

        # Vérifier les liens non autorisés — anti-link
        if (
            not violation_type
            and config.get("antilink_enabled")
        ):
            whitelist = config.get("antilink_whitelist") or []
            if not isinstance(whitelist, list):
                whitelist = []
            block_all = bool(config.get("antilink_block_all", False))
            bad_domain = _detect_unauthorized_link(content, whitelist, block_all=block_all)
            if bad_domain:
                violation_type = AutomodViolationType.URL_LINK
                violation_reason = f"Lien non autorisé : {bad_domain}"

        # Traiter la violation
        if violation_type and violation_reason:
            await self._handle_automod_violation(
                message,
                violation_type,
                violation_reason,
                censored_content,
                config,
            )

    async def _handle_automod_violation(
        self,
        message: discord.Message,
        violation_type: AutomodViolationType,
        reason: str,
        censored_content: str,
        config: dict,
    ) -> None:
        """Traite une violation d'automod avec DM + sanction enregistrée."""
        if not message.guild:
            return

        # Vérifier l'exemption hiérarchique complète
        member = message.author
        if isinstance(member, discord.Member):
            if self._is_automod_exempt(member, message.guild):
                return

        try:
            automod_action = config.get("automod_action", "warn")
            duration = int(config.get("automod_mute_duration", config.get("automod_duration", 10)) or 10)

            action_type_map = {
                "warn": SanctionType.WARN,
                "mute": SanctionType.MUTE,
                "kick": SanctionType.KICK,
                "ban":  SanctionType.BAN,
            }
            sanction_type = action_type_map.get(automod_action, SanctionType.WARN)
            sanction_id = generate_sanction_id(sanction_type)
            bot_id = self.bot.user.id if self.bot.user else 0
            full_reason = f"Automod: {reason}"

            color_map = {
                "warn": 0xffaa00,
                "mute": 0xff6600,
                "kick": 0xff5500,
                "ban":  0xff0000,
            }
            emoji_map = {
                "warn": "⚠️",
                "mute": "🔇",
                "kick": "👢",
                "ban":  "🚫",
            }
            label_map = {
                "warn": "Avertissement",
                "mute": f"Mise en sourdine ({duration} min)",
                "kick": "Expulsion",
                "ban":  "Bannissement",
            }
            color  = color_map.get(automod_action, 0xffaa00)
            emoji  = emoji_map.get(automod_action, "⚠️")
            label  = label_map.get(automod_action, "Sanction")

            # 1. Supprimer le message
            try:
                await message.delete()
            except Exception:
                pass

            # 2. DM direct à l'utilisateur — style DM distinctif
            try:
                dm_duration = f"{duration} minute(s)" if automod_action == "mute" else None
                dm_embed = build_dm_embed(
                    automod_action,
                    message.guild.name,
                    reason=full_reason,
                    duration=dm_duration,
                    sanction_id=sanction_id,
                    guild_id=message.guild.id,
                )
                dm_channel = await message.author.create_dm()
                await dm_channel.send(embed=dm_embed)
                logger.info(f"✅ DM Automod envoyé à {message.author.id}")
            except discord.Forbidden:
                logger.warning(f"⚠️ DM bloqué par {message.author.id} (DMs désactivés)")
            except Exception as e:
                logger.error(f"❌ Erreur DM Automod: {e}")

            # 3. Message dans le salon (embed, visible 10s)
            try:
                salon_embed = discord.Embed(
                    color=color,
                    description=f"{emoji} **{message.author.mention}** vient d'être sanctionné(e) par l'AutoMod.",
                )
                salon_embed.add_field(name="Sanction", value=label, inline=True)
                salon_embed.add_field(name="Raison", value=reason, inline=True)
                salon_embed.set_footer(text=f"🤖 AutoMod • ID {sanction_id}")
                await message.channel.send(embed=salon_embed)
            except Exception:
                pass

            # 4. Enregistrer la sanction
            try:
                add_warning(
                    message.guild.id,
                    message.author.id,
                    bot_id,
                    full_reason,
                    sanction_type.value,
                    sanction_id,
                )
            except Exception as e:
                logger.debug(f"Erreur enregistrement sanction automod: {e}")

            # 5. Log dans le canal de logs
            if isinstance(message.channel, (discord.TextChannel, discord.VoiceChannel)):
                await self._send_automod_log(
                    message.guild,
                    violation_type,
                    message.author,
                    message.channel,
                    reason,
                    censored_content,
                )

            # 6. Appliquer l'action Discord
            if isinstance(message.author, discord.Member):
                try:
                    if automod_action == "mute":
                        until = datetime.now(timezone.utc) + timedelta(minutes=duration)
                        await message.author.timeout(until, reason=full_reason)
                    elif automod_action == "kick":
                        await message.author.kick(reason=full_reason)
                    elif automod_action == "ban":
                        await message.author.ban(reason=full_reason)
                except Exception as e:
                    logger.debug(f"Erreur application action automod: {e}")

        except Exception as e:
            logger.exception(f"Erreur traitement violation automod: {e}")

    # ========================================================================
    # Commands - Moderation
    # ========================================================================
    @app_commands.command(name="mute", description="Rendre un membre muet")
    @app_commands.describe(membre="Le membre à rendre muet", duree="Durée en minutes", raison="Raison du mute")
    @app_commands.default_permissions(manage_messages=True)
    async def mute(self, interaction: discord.Interaction, membre: discord.Member, duree: int, raison: str):
        """Rend un membre muet pour une durée déterminée."""
        guild_id = interaction.guild.id if interaction.guild else 0
        await self._apply_sanction(
            interaction,
            membre,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.MUTE,
            minutes=duree,
        )

    @app_commands.command(name="ban", description="Bannir un membre ou par ID")
    @app_commands.describe(utilisateur="Le membre à bannir ou son ID", raison="Raison du ban")
    @app_commands.default_permissions(ban_members=True)
    async def ban(self, interaction: discord.Interaction, utilisateur: str, raison: str):
        """Bannit un membre du serveur (mention ou ID)."""
        guild_id = interaction.guild.id if interaction.guild else 0

        member_id = None

        # Essayer de résoudre l'utilisateur
        try:
            # Si c'est une mention <@123456>
            if utilisateur.startswith('<@') and utilisateur.endswith('>'):
                member_id = int(utilisateur[2:-1].replace('!', ''))
            else:
                # Essayer comme ID direct
                member_id = int(utilisateur)
        except ValueError:
            await interaction.response.send_message(
                embed=create_embed(
                    "❌ Utilisateur invalide",
                    "Veuillez fournir une mention (@user) ou un ID numérique valide",
                    0xff0000,
                ),
                ephemeral=True,
            )
            return

        await self._apply_sanction(
            interaction,
            member_id,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.BAN,
        )

    @app_commands.command(name="kick", description="Expulser un membre")
    @app_commands.describe(membre="Le membre à expulser", raison="Raison du kick")
    @app_commands.default_permissions(kick_members=True)
    async def kick(self, interaction: discord.Interaction, membre: discord.Member, raison: str):
        """Expulse un membre du serveur."""
        guild_id = interaction.guild.id if interaction.guild else 0
        await self._apply_sanction(
            interaction,
            membre,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.KICK,
        )

    @app_commands.command(name="warn", description="Avertir un membre")
    @app_commands.describe(membre="Le membre à avertir", raison="Raison de l'avertissement")
    @app_commands.default_permissions(manage_messages=True)
    async def warn(self, interaction: discord.Interaction, membre: discord.Member, raison: str):
        """Avertit un membre."""
        guild_id = interaction.guild.id if interaction.guild else 0
        await self._apply_sanction(
            interaction,
            membre,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.WARN,
        )

    @app_commands.command(name="unmute", description="Retirer le mute d'un membre")
    @app_commands.describe(membre="Le membre à démuter", raison="Raison du démuting")
    @app_commands.default_permissions(manage_messages=True)
    async def unmute(self, interaction: discord.Interaction, membre: discord.Member, raison: Optional[str] = None):
        """Retire le mute d'un membre."""
        guild_id = interaction.guild.id if interaction.guild else 0
        await self._apply_sanction(
            interaction,
            membre,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.UNMUTE,
        )

    @app_commands.command(name="unban", description="Débannir un membre par ID")
    @app_commands.describe(user_id="L'ID de l'utilisateur à débannir", raison="Raison du débannissement")
    @app_commands.default_permissions(ban_members=True)
    async def unban(self, interaction: discord.Interaction, user_id: str, raison: Optional[str] = None):
        """Débannit un utilisateur du serveur via son ID."""
        guild_id = interaction.guild.id if interaction.guild else 0

        # Convertir l'ID string en int
        try:
            user_id_int = int(user_id)
        except ValueError:
            await interaction.response.send_message(
                embed=create_embed(
                    "❌ ID invalide",
                    "Veuillez fournir un ID numérique valide",
                    0xff0000,
                ),
                ephemeral=True,
            )
            return

        await self._apply_sanction(
            interaction,
            user_id_int,
            raison or get_text(guild_id, "common.not_configured"),
            SanctionType.UNBAN,
        )

    @app_commands.command(name="sanction-liste", description="Voir la liste des sanctions d'un membre")
    @app_commands.describe(membre="Le membre dont vous voulez voir les sanctions")
    @app_commands.default_permissions(manage_messages=True)
    async def sanction_liste(self, interaction: discord.Interaction, membre: discord.Member):
        """Affiche la liste des sanctions d'un membre avec pagination."""
        try:
            if not interaction.guild:
                await interaction.response.send_message(
                    embed=create_embed(get_text(0, "common.guild_only"), "", 0xff0000),
                    ephemeral=True,
                )
                return

            warnings = get_warnings(interaction.guild.id, membre.id) or []
            if not warnings:
                embed = discord.Embed(
                    title=f"✅ Aucune sanction — {membre.display_name} (`{membre.name}`)",
                    description=f"{membre.mention} n'a aucune sanction enregistrée.",
                    color=0x22c55e,
                )
                if membre.display_avatar:
                    embed.set_thumbnail(url=membre.display_avatar.url)
                embed.set_footer(text=f"ID : {membre.id}")
                await interaction.response.send_message(embed=embed)
                return

            view = SanctionListeView(warnings, membre)
            await interaction.response.send_message(embed=view.build_embed(), view=view)

        except Exception as e:
            logger.exception("Erreur sanction_liste")
            guild_id = interaction.guild.id if interaction.guild else 0
            error_embed = create_embed(
                get_text(guild_id, "moderation.sanction_list_error"),
                get_text(guild_id, "moderation.sanction_list_error_desc", error=str(e)),
                0xff0000,
            )
            try:
                await interaction.response.send_message(embed=error_embed, ephemeral=True)
            except Exception:
                await interaction.followup.send(embed=error_embed, ephemeral=True)

    @app_commands.command(
        name="sanctions-serveur",
        description="Voir toutes les sanctions du serveur",
    )
    @app_commands.default_permissions(manage_messages=True)
    async def server_sanctions(self, interaction: Interaction) -> None:
        """Affiche toutes les sanctions du serveur."""
        if not interaction.guild:
            await interaction.response.send_message("❌ Cette commande ne fonctionne que sur un serveur", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            guild_id = interaction.guild.id

            # Charger toutes les sanctions
            from utils.database import DATA_FOLDER
            import os, json
            path = os.path.join(DATA_FOLDER, "warnings.json")

            all_sanctions = []
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    guild_sanctions = data.get(str(guild_id), {})

                    # Collecter toutes les sanctions avec infos utilisateur
                    for user_id_str, sanctions in guild_sanctions.items():
                        for sanction in sanctions:
                            try:
                                user_id = int(user_id_str)
                                user = interaction.guild.get_member(user_id)
                                username = user.mention if user else f"`{user_id_str}`"
                            except:
                                username = f"`{user_id_str}`"

                            all_sanctions.append({
                                "user_id": user_id_str,
                                "username": username,
                                "sanction": sanction
                            })
            except:
                pass

            if not all_sanctions:
                embed = discord.Embed(
                    title="📋 Sanctions du Serveur",
                    description="✅ Aucune sanction enregistrée",
                    color=0x00ff00
                )
                await interaction.followup.send(embed=embed)
                return

            # Créer des embeds avec max 10 sanctions par embed
            embeds = []
            for i in range(0, len(all_sanctions), 10):
                chunk = all_sanctions[i:i+10]
                embed = discord.Embed(
                    title="📋 Sanctions du Serveur",
                    color=0xff9900,
                    description=f"Page {len(embeds) + 1} ({len(all_sanctions)} sanctions totales)"
                )

                for item in chunk:
                    user = item["username"]
                    sanction = item["sanction"]

                    sanction_type = sanction.get("type", "warn")
                    sanction_reason = sanction.get("reason", "Pas de raison")
                    sanction_id = sanction.get("sanction_id") or sanction.get("id", "-")
                    timestamp = sanction.get("timestamp", "Date inconnue")

                    if timestamp and timestamp != "Date inconnue":
                        try:
                            timestamp = datetime.fromisoformat(timestamp).strftime("%d/%m/%Y %H:%M")
                        except:
                            pass

                    # Emoji selon le type
                    type_emoji = {
                        "warn": "⚠️",
                        "mute": "🔇",
                        "ban": "🔨",
                        "kick": "👢",
                        "unmute": "🔊",
                        "unban": "🔓",
                        "auto_mod": "🛡️"
                    }.get(sanction_type, "📌")

                    field_name = f"{type_emoji} {user} [{sanction_id}]"
                    field_value = f"**Raison:** {sanction_reason}\n**Date:** {timestamp}"

                    embed.add_field(name=field_name, value=field_value, inline=False)

                embed.set_footer(text=f"Miaouscle Bot • Total: {len(all_sanctions)} sanctions")
                embeds.append(embed)

            # Envoyer le premier embed
            await interaction.followup.send(embed=embeds[0])

            # Envoyer les autres si plusieurs pages
            for embed in embeds[1:]:
                await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.exception("Erreur server_sanctions")
            embed = discord.Embed(
                title="❌ Erreur",
                description=f"Impossible d'afficher les sanctions: {str(e)}",
                color=0xff0000
            )
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.NotFound:
                pass

    @app_commands.command(
        name="supprimer-sanction",
        description="Supprimer une sanction par son ID",
    )
    @app_commands.describe(user="Utilisateur sanctionné", sanction_id="ID de la sanction à supprimer (ex: W-251130-A7X2K)")
    async def delete_sanction(self, interaction: Interaction, user: discord.User, sanction_id: str) -> None:
        """Supprime une sanction d'un membre par son ID."""
        if not interaction.guild:
            await interaction.response.send_message("❌ Cette commande ne fonctionne que sur un serveur", ephemeral=True)
            return

        await interaction.response.defer()

        try:
            guild_id = interaction.guild.id
            warnings = get_warnings(guild_id, user.id)

            # Chercher la sanction par ID
            found = False
            deleted_sanction: Optional[dict] = None

            for i, warning in enumerate(warnings):
                if warning.get("sanction_id") == sanction_id or warning.get("id") == sanction_id:
                    deleted_sanction = warning
                    warnings.pop(i)
                    found = True
                    break

            if not found:
                embed = discord.Embed(
                    title="❌ Sanction Non Trouvée",
                    description=f"Aucune sanction avec l'ID **{sanction_id}** trouvée pour {user.mention}",
                    color=0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            # Sauvegarder les changements
            from utils.database import DATA_FOLDER
            import os, json
            path = os.path.join(DATA_FOLDER, "warnings.json")
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
            except:
                data = {}

            if str(guild_id) not in data:
                data[str(guild_id)] = {}
            data[str(guild_id)][str(user.id)] = warnings

            with open(path, 'w', encoding='utf-8') as f:
                json.dump(data, f, indent=2, ensure_ascii=False)

            # Confirmation
            sanction_type = deleted_sanction.get("type", "sanction") if deleted_sanction else "sanction"
            reason = deleted_sanction.get("reason", "Pas de raison") if deleted_sanction else "Pas de raison"

            embed = discord.Embed(
                title="✅ Sanction Supprimée",
                description=f"La sanction **{sanction_id}** a été supprimée avec succès pour {user.mention}",
                color=0x00ff00
            )
            embed.add_field(name="📍 ID", value=sanction_id, inline=True)
            embed.add_field(name="🏷️ Type", value=sanction_type, inline=True)
            embed.add_field(name="📝 Raison", value=reason, inline=False)
            embed.set_footer(text=f"Supprimée par {interaction.user.name}")

            await interaction.followup.send(embed=embed)

        except Exception as e:
            logger.exception("Erreur supprimer_sanction")
            embed = discord.Embed(
                title="❌ Erreur",
                description=f"Impossible de supprimer la sanction: {str(e)}",
                color=0xff0000
            )
            try:
                await interaction.followup.send(embed=embed, ephemeral=True)
            except discord.NotFound:
                pass


# ============================================================================
# Setup
# ============================================================================
async def setup(bot: commands.Bot):
    """Charge le cog de modération."""
    await bot.add_cog(Moderation(bot))
