"""
cogs/stats_collector.py — Collecte des statistiques journalières par serveur.

Pour chaque serveur où le bot est présent, on enregistre une entrée par jour
dans `web/data/stats_daily/{gid}.jsonl` :
- members      : snapshot du nombre de membres en cache à l'instant du flush
- messages     : nombre de messages reçus dans les 24h glissantes (compteur en
                 mémoire incrémenté par on_message)
- sanctions    : nombre de sanctions du jour (incrémenté côté moderation via
                 utils/stats_daily.increment_sanctions ; ce cog ne fait rien
                 pour ce compteur, il sert juste à snapshot members + messages)

Tâche périodique : flush toutes les heures (suffisant pour des courbes 30j),
+ snapshot final à minuit UTC.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, time, timezone

import discord
from discord.ext import commands, tasks

from utils.stats_daily import upsert_today

logger = logging.getLogger(__name__)


class StatsCollectorCog(commands.Cog):
    """Compte les messages en mémoire et flush les snapshots quotidiens."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        # Compteur en mémoire des messages reçus depuis le dernier flush
        self._message_counts: dict[int, int] = defaultdict(int)

    async def cog_load(self):
        if not self.flush_loop.is_running():
            self.flush_loop.start()

    def cog_unload(self):
        self.flush_loop.cancel()

    # ------------------------------------------------------------ listeners

    @commands.Cog.listener()
    async def on_message(self, message: discord.Message):
        if message.guild is None or message.author.bot:
            return
        self._message_counts[message.guild.id] += 1

    # ---------------------------------------------------- tâche périodique

    @tasks.loop(minutes=60)
    async def flush_loop(self):
        """Flush les compteurs en mémoire vers le stockage JSONL."""
        if not self.bot.is_ready():
            return
        try:
            for guild in list(self.bot.guilds):
                gid = guild.id
                msg_inc = self._message_counts.pop(gid, 0)
                # Snapshot members
                member_count = guild.member_count or 0
                # Lire l'entrée du jour pour cumuler messages
                from utils.stats_daily import read_history
                history = read_history(gid, days=1)
                today = datetime.now(timezone.utc).date().isoformat()
                cur = next((e for e in history if e.get("date") == today), None)
                cur_msg = (cur or {}).get("messages", 0)
                upsert_today(
                    gid,
                    members=member_count,
                    messages=cur_msg + msg_inc,
                )
        except Exception as e:
            logger.error(f"flush_loop stats : {e}", exc_info=True)

    @flush_loop.before_loop
    async def before_flush(self):
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot):
    await bot.add_cog(StatsCollectorCog(bot))
