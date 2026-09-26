"""
Synchronisation des rôles Discord Premium / Premium+ dans le serveur de support.

Logique :
    - Un utilisateur "mérite" un rôle s'il a sponsorisé (champ `granted_by`)
      au moins un serveur ayant un Premium actif.
    - On lui donne le rôle correspondant au tier MAX qu'il a sponsorisé
      (premium_plus > premium > free).
    - On retire automatiquement les rôles s'il n'a plus aucun Premium actif
      (expiration ou révocation).

Idempotent : appeler `sync_user_roles` plusieurs fois ne fait rien si l'état
est déjà bon. Sûr à appeler en boucle (tâche périodique, on_member_join, etc.).
"""

import logging
from typing import Optional

import discord

from utils.premium import (
    _load,
    _is_active,
    TIER_FREE,
    TIER_PREMIUM,
    TIER_PREMIUM_PLUS,
    TIER_ORDER,
    PREMIUM_ROLE_ID,
    PREMIUM_PLUS_ROLE_ID,
    SUPPORT_GUILD_ID,
)

logger = logging.getLogger(__name__)

# Rôles attribués par tier — Premium+ hérite aussi du rôle Premium
ROLES_BY_TIER = {
    TIER_PREMIUM: {PREMIUM_ROLE_ID},
    TIER_PREMIUM_PLUS: {PREMIUM_ROLE_ID, PREMIUM_PLUS_ROLE_ID},
}
ALL_PREMIUM_ROLE_IDS = {PREMIUM_ROLE_ID, PREMIUM_PLUS_ROLE_ID}


def compute_user_max_tier(user_id: int) -> str:
    """Calcule le tier Premium maximum 'mérité' par cet utilisateur.

    Considère :
        - son enregistrement personnel `users.<id>` (legacy) s'il est actif
        - tous les serveurs dont il est `granted_by` ET qui ont un abonnement
          Premium actif (non expiré)

    Retourne le label du tier max ('free' / 'premium' / 'premium_plus').
    """
    data = _load()
    max_tier = TIER_FREE
    max_rank = TIER_ORDER[TIER_FREE]
    user_id_str = str(user_id)

    # 1. Premium personnel (legacy, généralement vide)
    user_record = data["users"].get(user_id_str)
    if _is_active(user_record):
        tier = user_record.get("tier", TIER_FREE)
        rank = TIER_ORDER.get(tier, 0)
        if rank > max_rank:
            max_tier, max_rank = tier, rank

    # 2. Tous les serveurs sponsorisés
    for guild_record in data["guilds"].values():
        if not _is_active(guild_record):
            continue
        if guild_record.get("granted_by") != user_id_str:
            continue
        tier = guild_record.get("tier", TIER_FREE)
        rank = TIER_ORDER.get(tier, 0)
        if rank > max_rank:
            max_tier, max_rank = tier, rank

    return max_tier


def collect_known_user_ids() -> set[int]:
    """Retourne tous les user_ids référencés dans premium.json
    (users.* + guilds.*.granted_by) qui ont au moins un enregistrement actif."""
    data = _load()
    out: set[int] = set()
    for uid, rec in data["users"].items():
        if _is_active(rec):
            try:
                out.add(int(uid))
            except (TypeError, ValueError):
                pass
    for grec in data["guilds"].values():
        if not _is_active(grec):
            continue
        gb = grec.get("granted_by")
        if gb:
            try:
                out.add(int(gb))
            except (TypeError, ValueError):
                pass
    return out


async def _resolve_member(
    guild: discord.Guild, user_id: int
) -> Optional[discord.Member]:
    """Récupère un member, en passant par fetch si pas en cache."""
    member = guild.get_member(user_id)
    if member is not None:
        return member
    try:
        return await guild.fetch_member(user_id)
    except discord.NotFound:
        return None
    except discord.HTTPException as e:
        logger.warning(f"fetch_member({user_id}) erreur API : {e}")
        return None


async def sync_user_roles(
    bot: discord.Client, user_id: int
) -> Optional[str]:
    """Synchronise les rôles Premium de cet utilisateur dans le serveur de support.

    Retourne :
        - le tier appliqué ('free', 'premium', 'premium_plus') si OK
        - None si on n'a pas pu (serveur introuvable, user pas membre, perms)
    """
    guild = bot.get_guild(SUPPORT_GUILD_ID)
    if guild is None:
        logger.warning(
            f"sync_user_roles: serveur de support {SUPPORT_GUILD_ID} introuvable "
            "(le bot n'y est peut-être pas)."
        )
        return None

    member = await _resolve_member(guild, user_id)
    if member is None:
        logger.info(
            f"sync_user_roles: user {user_id} pas membre du serveur de support — skip."
        )
        return None

    target_tier = compute_user_max_tier(user_id)
    # Ensemble des IDs de rôles à posséder selon le tier (vide si free)
    target_role_ids: set[int] = ROLES_BY_TIER.get(target_tier, set())

    current_premium_role_ids = {
        r.id for r in member.roles if r.id in ALL_PREMIUM_ROLE_IDS
    }
    role_ids_to_remove = current_premium_role_ids - target_role_ids
    role_ids_to_add = target_role_ids - current_premium_role_ids

    if not role_ids_to_remove and not role_ids_to_add:
        return target_tier  # Déjà à jour

    # Résolution des objets discord.Role à ajouter
    target_roles_to_add: list[discord.Role] = []
    for rid in role_ids_to_add:
        r = guild.get_role(rid)
        if r is None:
            logger.warning(
                f"sync_user_roles: rôle {rid} introuvable dans le serveur — "
                "vérifier l'ID dans utils/premium.py."
            )
            return None
        target_roles_to_add.append(r)

    # Construit la liste finale des rôles : tous les rôles non-Premium actuels
    # + les rôles Premium cibles. Une seule API call atomique.
    final_roles = [r for r in member.roles if r.id not in ALL_PREMIUM_ROLE_IDS]
    final_roles.extend(target_roles_to_add)
    # Conserver aussi les rôles Premium déjà présents qu'on garde
    for rid in (current_premium_role_ids & target_role_ids):
        existing = guild.get_role(rid)
        if existing:
            final_roles.append(existing)

    try:
        await member.edit(roles=final_roles, reason=f"Sync Premium → {target_tier}")
    except discord.Forbidden:
        logger.error(
            f"❌ sync_user_roles: PERMISSION REFUSÉE pour modifier les rôles de "
            f"user {user_id} dans le serveur de support {SUPPORT_GUILD_ID}. "
            "Vérifier que (1) le bot a la permission 'Gérer les rôles' et "
            "(2) son rôle est positionné AU-DESSUS des rôles Premium dans la "
            "hiérarchie du serveur."
        )
        return None
    except discord.HTTPException as e:
        logger.warning(f"sync_user_roles: erreur API : {e}")
        return None

    logger.info(
        f"✅ Rôles Premium sync pour user {user_id} → tier={target_tier} "
        f"(retirés={sorted(role_ids_to_remove)}, ajoutés={sorted(r.id for r in target_roles_to_add)})"
    )
    return target_tier


async def sync_all_users(bot: discord.Client) -> dict:
    """Re-sync tous les utilisateurs ayant un Premium actif.

    Idempotent. Utilisé au démarrage du bot et via la commande
    `/premium-admin sync-roles` pour rattraper les changements (expirations,
    grants faits depuis le dashboard, etc.).
    """
    user_ids = collect_known_user_ids()
    summary = {"synced": 0, "skipped": 0, "total": len(user_ids)}
    for uid in user_ids:
        result = await sync_user_roles(bot, uid)
        if result is None:
            summary["skipped"] += 1
        else:
            summary["synced"] += 1
    logger.info(f"✅ Sync Premium roles global terminé : {summary}")
    return summary
