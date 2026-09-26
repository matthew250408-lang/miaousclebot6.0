"""
Cog de synchronisation automatique des rôles Premium dans le serveur de support.

Quatre mécanismes :
    1. Au boot (10s après on_ready) : sync de TOUS les utilisateurs ayant un
       Premium actif. Rattrape les changements faits pendant que le bot était
       offline (expirations, grants depuis le dashboard, etc.).
    2. on_member_join : si quelqu'un (re-)rejoint le serveur de support et
       qu'il a un Premium actif, il récupère son rôle automatiquement.
    3. Tâche rapide toutes les 30 secondes : lit le fichier de queue
       `web/data/pending_role_syncs.json` écrit par Flask après un paiement
       confirmé — sync immédiate de l'acheteur sans attendre la tâche longue.
    4. Tâche périodique toutes les 30 minutes : rattrape les modifications
       faites depuis le dashboard ou autres sources hors-bot.

Le cog `premium_admin` appelle aussi `sync_user_roles` directement après
chaque grant/revoke pour une sync immédiate.
"""

import asyncio
import json
import logging
import os

import discord
from discord.ext import commands, tasks

from utils.premium import SUPPORT_GUILD_ID, PREMIUM_ROLE_ID, PREMIUM_PLUS_ROLE_ID
from utils.premium_roles import sync_all_users, sync_user_roles

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PENDING_SYNC_FILE = os.path.join(_PROJECT_ROOT, 'web', 'data', 'pending_role_syncs.json')


def _pop_pending_syncs() -> list[int]:
    """Lit et vide atomiquement le fichier de queue des syncs en attente."""
    try:
        if not os.path.exists(PENDING_SYNC_FILE):
            return []
        with open(PENDING_SYNC_FILE, 'r', encoding='utf-8') as f:
            data = json.load(f)
        if not isinstance(data, list) or not data:
            return []
        # Vide le fichier immédiatement pour éviter les doubles syncs
        with open(PENDING_SYNC_FILE, 'w', encoding='utf-8') as f:
            json.dump([], f)
        return [int(uid) for uid in data if uid]
    except Exception as e:
        logger.warning(f"_pop_pending_syncs erreur : {e}")
        return []


class PremiumRolesSyncCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._initial_sync_done = False
        self._roles_diagnosed = False
        self.quick_sync.start()
        self.periodic_sync.start()

    def cog_unload(self):
        self.quick_sync.cancel()
        self.periodic_sync.cancel()

    @commands.Cog.listener()
    async def on_ready(self):
        # Évite de re-sync à chaque reconnexion gateway (on_ready peut être appelé
        # plusieurs fois si Discord redémarre la session)
        if self._initial_sync_done:
            return
        self._initial_sync_done = True
        # Petite attente pour que le cache des guilds/membres se peuple
        await asyncio.sleep(10)
        try:
            summary = await sync_all_users(self.bot)
            logger.info(f"🔄 Initial sync rôles Premium au boot : {summary}")
        except Exception as e:
            logger.exception(f"Erreur lors du sync Premium initial : {e}")

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        # Seulement dans le serveur de support
        if member.guild.id != SUPPORT_GUILD_ID:
            return
        try:
            tier = await sync_user_roles(self.bot, member.id)
            if tier and tier != "free":
                logger.info(
                    f"🎁 Rôle Premium auto-restauré pour {member.id} (tier={tier}) "
                    "à son entrée dans le serveur de support."
                )
        except Exception as e:
            logger.exception(f"Erreur sync_user_roles on_member_join({member.id}) : {e}")

    @tasks.loop(seconds=30)
    async def quick_sync(self):
        """Sync immédiate des utilisateurs en attente suite à un paiement."""
        pending = _pop_pending_syncs()
        if not pending:
            return
        for uid in pending:
            try:
                tier = await sync_user_roles(self.bot, uid)
                logger.info(f"⚡ Sync rapide rôle Premium pour user {uid} → tier={tier}")
            except Exception as e:
                logger.warning(f"quick_sync erreur pour user {uid} : {e}")

    @quick_sync.before_loop
    async def _wait_ready_quick(self):
        await self.bot.wait_until_ready()

    @tasks.loop(minutes=30)
    async def periodic_sync(self):
        """Rattrape les changements de Premium faits hors du bot (dashboard, etc.)."""
        # Diagnostic des IDs de rôles au premier appel
        if not self._roles_diagnosed:
            self._roles_diagnosed = True
            guild = self.bot.get_guild(SUPPORT_GUILD_ID)
            if guild is None:
                logger.warning(f"⚠️ [PremiumSync] Serveur de support {SUPPORT_GUILD_ID} introuvable.")
            else:
                role_p = guild.get_role(PREMIUM_ROLE_ID)
                role_pp = guild.get_role(PREMIUM_PLUS_ROLE_ID)
                logger.info(
                    f"🔍 [PremiumSync] Vérification rôles dans '{guild.name}' :\n"
                    f"   • PREMIUM_ROLE_ID={PREMIUM_ROLE_ID} → "
                    f"{'✅ ' + role_p.name if role_p else '❌ INTROUVABLE — ID incorrect ou rôle supprimé !'}\n"
                    f"   • PREMIUM_PLUS_ROLE_ID={PREMIUM_PLUS_ROLE_ID} → "
                    f"{'✅ ' + role_pp.name if role_pp else '❌ INTROUVABLE — ID incorrect ou rôle supprimé !'}"
                )
        try:
            await sync_all_users(self.bot)
        except Exception as e:
            logger.exception(f"Erreur sync Premium périodique : {e}")

    @periodic_sync.before_loop
    async def _wait_ready(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PremiumRolesSyncCog(bot))
