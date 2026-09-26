"""
Système Premium Miaouscle Bot.

Modèle: Premium UNIQUEMENT au niveau serveur (depuis v13.0.0).
Une fois un serveur Premium, tous ses membres et admins en profitent.

Les `OWNER_IDS` (propriétaires du bot) servent UNIQUEMENT au contrôle
d'accès des commandes d'administration internes (/premium-admin, etc.).
Ils n'octroient AUCUN tier Premium et ne bypassent PAS le gating des
features — seul le tier du serveur compte.

Seul cas particulier: les serveurs listés dans `FREE_PREMIUM_GUILDS`
bénéficient d'un Premium+ gratuit et permanent (offerts manuellement).

Le stockage `users.<id>` reste dans le fichier JSON pour compatibilité
historique mais n'est plus consulté pour le gating runtime.

Stockage: web/data/premium.json
{
    "users": {
        "<user_id>": {
            "tier": "premium" | "premium_plus",
            "since": "ISO datetime UTC",
            "expires": "ISO datetime UTC" | null  (null = à vie / lifetime)
        }
    },
    "guilds": {
        "<guild_id>": {
            "tier": "premium" | "premium_plus",
            "since": "ISO datetime UTC",
            "expires": "ISO datetime UTC" | null,
            "granted_by": "<user_id>"
        }
    }
}

Hiérarchie: premium_plus > premium > free
"""
import json
import os
import functools
from datetime import datetime, timezone
from typing import Optional

import discord
from discord import app_commands

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PREMIUM_FILE = os.path.join(_PROJECT_ROOT, "web", "data", "premium.json")
os.makedirs(os.path.dirname(PREMIUM_FILE), exist_ok=True)

OWNER_IDS = {653038666625785866, 1140296215469240401}

# Serveurs ayant un accès Premium+ GRATUIT et permanent (offerts manuellement)
FREE_PREMIUM_GUILDS = {1188506879052238988}

# Serveur de support / hub Miaouscle : c'est dans CE serveur que les rôles
# Premium / Premium+ sont attribués aux utilisateurs qui ont sponsorisé un
# Premium pour un serveur (champ `granted_by`).
SUPPORT_GUILD_ID = 1188506879052238988

# Rôles Discord à attribuer dans le serveur de support selon le tier
# possédé par l'utilisateur.
PREMIUM_ROLE_ID = 1497725620870844551
PREMIUM_PLUS_ROLE_ID = 1497725622254829718

TIER_FREE = "free"
TIER_PREMIUM = "premium"
TIER_PREMIUM_PLUS = "premium_plus"

TIER_ORDER = {TIER_FREE: 0, TIER_PREMIUM: 1, TIER_PREMIUM_PLUS: 2}

TIER_LABELS = {
    TIER_FREE: "🆓 Gratuit",
    TIER_PREMIUM: "💎 Premium",
    TIER_PREMIUM_PLUS: "👑 Premium+",
}

# Champs de configuration réservés aux serveurs Premium.
# Servent à :
#   - filtrer les POST côté API (impossible de sauver ces clés sans Premium)
#   - afficher un cadenas/lock dans le dashboard
#   - bloquer l'édition via /config slash si pas Premium
PREMIUM_ONLY_CONFIG_KEYS = {
    # Personnalisation Bienvenue
    "welcome_title", "welcome_color", "welcome_image",
    "welcome_footer", "welcome_ping",
    # Personnalisation Au revoir
    "goodbye_title", "goodbye_color", "goodbye_image",
    "goodbye_footer", "goodbye_ping",
    # Anti-Raid (toute la sous-config)
    "anti_raid",
    # Cartes de niveau personnalisées (Premium)
    "rank_bg_color", "rank_bar_start_color", "rank_bar_end_color",
    "rank_text_color", "rank_bg_image",
}

PREMIUM_PLUS_ONLY_CONFIG_KEYS = {
    # MP de bienvenue automatique
    "welcome_dm_enabled", "welcome_dm_message",
}


def filter_premium_config(
    incoming: dict,
    guild_id: Optional[int],
    user_id: int,
) -> tuple[dict, list[str]]:
    """
    Filtre un dict de config: retire les clés Premium si le contexte n'a pas le tier requis.
    Retourne (config_filtrée, liste_clés_rejetées).
    """
    if not isinstance(incoming, dict):
        return incoming, []
    rejected: list[str] = []
    out: dict = {}
    has_p = has_premium(guild_id, user_id, TIER_PREMIUM)
    has_pp = has_premium(guild_id, user_id, TIER_PREMIUM_PLUS)
    for k, v in incoming.items():
        if k in PREMIUM_PLUS_ONLY_CONFIG_KEYS and not has_pp:
            rejected.append(k)
            continue
        if k in PREMIUM_ONLY_CONFIG_KEYS and not has_p:
            rejected.append(k)
            continue
        out[k] = v
    return out, rejected


def _load() -> dict:
    """Charge le fichier premium.json en garantissant la structure."""
    try:
        with open(PREMIUM_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        data = {}
    data.setdefault("users", {})
    data.setdefault("guilds", {})
    return data


def _save(data: dict) -> None:
    with open(PREMIUM_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def _is_active(record: Optional[dict]) -> bool:
    """Un abonnement est actif si pas de date d'expiration OU expire dans le futur."""
    if not record:
        return False
    expires = record.get("expires")
    if not expires:
        return True
    try:
        exp_dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
        return exp_dt > datetime.now(timezone.utc)
    except (ValueError, AttributeError):
        return False


def get_user_tier(user_id: int) -> str:
    """Retourne le tier Premium d'un utilisateur (ou 'free').

    Conservé pour compatibilité historique. Le Premium fonctionne désormais
    uniquement par serveur — cette fonction renseigne juste l'état brut
    enregistré dans `users.<id>` et n'est PAS utilisée pour le gating.
    """
    data = _load()
    record = data["users"].get(str(user_id))
    if _is_active(record):
        return record.get("tier", TIER_FREE)
    return TIER_FREE


def get_guild_tier(guild_id: int) -> str:
    """Retourne le tier Premium d'un serveur (ou 'free')."""
    if guild_id in FREE_PREMIUM_GUILDS:
        return TIER_PREMIUM_PLUS
    data = _load()
    record = data["guilds"].get(str(guild_id))
    if _is_active(record):
        return record.get("tier", TIER_FREE)
    return TIER_FREE


def is_free_premium_guild(guild_id: int) -> bool:
    """Indique si le serveur bénéficie d'un Premium+ gratuit offert."""
    return guild_id in FREE_PREMIUM_GUILDS


_TIER_RANK = {TIER_FREE: 0, TIER_PREMIUM: 1, TIER_PREMIUM_PLUS: 2}


def get_user_customer_tier(bot, user_id: int) -> str:
    """Retourne le tier le plus élevé qu'un utilisateur 'possède' à travers
    les serveurs où il est administrateur (donc considéré comme client payant).

    Les serveurs en Premium+ gratuit offert (ex: serveur de support) sont
    exclus pour éviter de promouvoir tout le monde à Premium+ uniquement
    parce qu'ils sont membres du serveur de support.

    Cas particulier : les **propriétaires du bot** (`OWNER_IDS`) reçoivent
    automatiquement le tier le plus élevé (`TIER_PREMIUM_PLUS`) puisque c'est
    leur bot — sans cela, ils retomberaient en Premium si leur seul serveur
    Premium+ est le serveur de support (qui est exclu).

    Renvoie TIER_FREE si l'utilisateur n'est admin d'aucun serveur Premium payant.
    """
    if user_id in OWNER_IDS:
        return TIER_PREMIUM_PLUS

    best_rank = 0
    best_tier = TIER_FREE
    for guild in getattr(bot, "guilds", []):
        if is_free_premium_guild(guild.id):
            continue
        member = guild.get_member(user_id)
        if member is None:
            continue
        if not member.guild_permissions.administrator:
            continue
        tier = get_guild_tier(guild.id)
        rank = _TIER_RANK.get(tier, 0)
        if rank > best_rank:
            best_rank = rank
            best_tier = tier
            if best_tier == TIER_PREMIUM_PLUS:
                break
    return best_tier


def get_effective_tier(guild_id: Optional[int], user_id: int = 0) -> str:
    """
    Le Premium fonctionne UNIQUEMENT au niveau serveur.
    Le tier d'un utilisateur n'est PAS pris en compte pour les features
    (les `OWNER_IDS` du bot ne bypassent pas non plus — ils n'ont accès
    qu'aux commandes d'administration internes).

    Le paramètre `user_id` est conservé pour rétro-compatibilité de signature
    mais est ignoré pour la résolution du tier.
    """
    return get_guild_tier(guild_id) if guild_id else TIER_FREE


def has_premium(guild_id: Optional[int], user_id: int = 0, min_tier: str = TIER_PREMIUM) -> bool:
    """Vérifie si le serveur a au moins le tier requis (Premium par serveur).

    Le paramètre `user_id` est conservé pour rétro-compatibilité de signature
    mais est ignoré: seul le tier du serveur compte.
    """
    effective = get_effective_tier(guild_id, user_id)
    return TIER_ORDER[effective] >= TIER_ORDER[min_tier]


def grant_premium(
    target_id: int,
    target_type: str = "user",
    tier: str = TIER_PREMIUM,
    expires: Optional[datetime] = None,
    granted_by: Optional[int] = None,
) -> dict:
    """Accorde le Premium à un utilisateur ou un serveur."""
    if target_type not in ("user", "guild"):
        raise ValueError("target_type doit être 'user' ou 'guild'")
    if tier not in (TIER_PREMIUM, TIER_PREMIUM_PLUS):
        raise ValueError("tier doit être 'premium' ou 'premium_plus'")

    data = _load()
    key = "users" if target_type == "user" else "guilds"
    record = {
        "tier": tier,
        "since": datetime.now(timezone.utc).isoformat(),
        "expires": expires.isoformat() if expires else None,
    }
    if granted_by:
        record["granted_by"] = str(granted_by)
    data[key][str(target_id)] = record
    _save(data)
    return record


def revoke_premium(target_id: int, target_type: str = "user") -> bool:
    """Révoque le Premium d'un utilisateur ou d'un serveur. True si quelque chose a été retiré."""
    data = _load()
    key = "users" if target_type == "user" else "guilds"
    if str(target_id) in data[key]:
        del data[key][str(target_id)]
        _save(data)
        return True
    return False


def get_record(target_id: int, target_type: str = "user") -> Optional[dict]:
    """Retourne l'enregistrement Premium brut (utile pour afficher la date d'expiration)."""
    data = _load()
    key = "users" if target_type == "user" else "guilds"
    return data[key].get(str(target_id))


def premium_only(min_tier: str = TIER_PREMIUM):
    """
    Décorateur pour les commandes slash: vérifie que le serveur a au moins
    le tier Premium requis (modèle par serveur). Les `OWNER_IDS` du bot ne
    bypassent PAS ce check. Sinon affiche un message d'invitation.
    """
    def decorator(func):
        @functools.wraps(func)
        async def wrapper(self, interaction: discord.Interaction, *args, **kwargs):
            guild_id = interaction.guild.id if interaction.guild else None
            user_id = interaction.user.id
            if not has_premium(guild_id, user_id, min_tier):
                tier_label = TIER_LABELS.get(min_tier, min_tier)
                embed = discord.Embed(
                    title="🔒 Fonctionnalité Premium",
                    description=(
                        f"Cette commande est réservée aux abonnés **{tier_label}**.\n\n"
                        "Découvrez tous les avantages Premium et passez à l'abonnement "
                        "pour débloquer cette fonctionnalité."
                    ),
                    color=0xfbbf24,
                )
                embed.set_footer(text="Miaouscle Bot Premium")
                view = discord.ui.View()
                premium_url = "https://a7a54eed-3c9d-44cc-9bb5-f542e450c08d-00-7wjyd8rom8cz.janeway.replit.dev/premium"
                view.add_item(discord.ui.Button(
                    label="Voir les tarifs",
                    url=premium_url,
                    emoji="💎",
                ))
                await interaction.response.send_message(embed=embed, view=view, ephemeral=True)
                return
            return await func(self, interaction, *args, **kwargs)
        return wrapper
    return decorator
