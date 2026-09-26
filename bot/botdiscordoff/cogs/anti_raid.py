"""
Module Anti-Raid Premium — Une seule commande `/anti-raid` avec toutes les options.

Détection: si N nouveaux membres rejoignent en < W secondes → raid détecté.
Comptes < X jours d'âge sont considérés comme suspects.

Réponse configurable: log / kick / ban / lockdown.
Hiérarchie Discord respectée: propriétaire, admins, modérateurs et membres
avec un rôle >= bot sont toujours ignorés/protégés.
"""
import logging
from collections import deque
from datetime import datetime, timezone
from typing import Dict, Deque, Optional

import discord
from discord import app_commands
from discord.ext import commands

from utils.database import load_config, save_config
from utils.premium import (
    has_premium,
    premium_only,
    TIER_PREMIUM,
    OWNER_IDS,
)

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "enabled": False,
    "threshold": 5,
    "window": 10,
    "min_account_age_days": 7,
    "action": "lockdown",
    "log_channel_id": None,
}

VALID_ACTIONS = {"log", "kick", "ban", "lockdown"}


def _is_protected(member: discord.Member, guild: discord.Guild) -> bool:
    """
    Retourne True si le membre ne doit JAMAIS être sanctionné par l'anti-raid.
    Respecte la hiérarchie Discord complète.
    """
    if member.bot:
        return True
    if guild.owner_id == member.id:
        return True
    perms = member.guild_permissions
    if perms.administrator:
        return True
    if perms.manage_guild or perms.manage_messages or perms.kick_members or perms.ban_members:
        return True
    # Rôle supérieur ou égal au bot → le bot ne peut pas agir sur eux
    bot_member = guild.me
    if bot_member and member.top_role >= bot_member.top_role:
        return True
    return False


class AntiRaidCog(commands.Cog):
    """Système Anti-Raid Premium en une seule commande."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self._joins: Dict[int, Deque[tuple]] = {}
        self._lockdown_active: Dict[int, bool] = {}

    def _get_config(self, guild_id: int) -> dict:
        full_config = load_config(guild_id)
        ar = full_config.get("anti_raid", {})
        return {**DEFAULT_CONFIG, **ar}

    def _save_config(self, guild_id: int, ar_config: dict) -> None:
        full_config = load_config(guild_id)
        full_config["anti_raid"] = ar_config
        save_config(guild_id, full_config)

    def _is_suspicious(self, member: discord.Member, min_age_days: int) -> bool:
        if not member.created_at:
            return True
        age_days = (datetime.now(timezone.utc) - member.created_at).days
        return age_days < min_age_days

    async def _get_log_channel(self, guild: discord.Guild, config: dict) -> Optional[discord.TextChannel]:
        channel_id = config.get("log_channel_id")
        if channel_id:
            ch = guild.get_channel(int(channel_id))
            if isinstance(ch, discord.TextChannel):
                return ch
        for ch in guild.text_channels:
            if ch.permissions_for(guild.me).send_messages:
                return ch
        return None

    async def _trigger_action(self, guild: discord.Guild, config: dict, suspects: list) -> str:
        """
        Applique l'action configurée sur les suspects.
        Les membres protégés (proprio, admins, rôle >= bot) sont ignorés.
        """
        action = config.get("action", "lockdown")
        if action not in VALID_ACTIONS:
            action = "lockdown"

        if action == "log":
            return f"🔔 **Mode log** — {len(suspects)} suspect(s) détecté(s)."

        if action in ("kick", "ban"):
            success, failed, skipped = 0, 0, 0
            for member in suspects:
                # Vérification hiérarchie avant toute action
                if _is_protected(member, guild):
                    skipped += 1
                    logger.info(f"Anti-Raid: membre protégé ignoré → {member} ({member.id})")
                    continue
                try:
                    if action == "kick":
                        await member.kick(reason="Anti-Raid: vague suspecte")
                    else:
                        await member.ban(reason="Anti-Raid: vague suspecte", delete_message_days=1)
                    success += 1
                except discord.Forbidden:
                    failed += 1
                    logger.warning(f"Anti-Raid: Forbidden pour {action} sur {member.id}")
                except discord.HTTPException as e:
                    failed += 1
                    logger.warning(f"Anti-Raid: HTTPException pour {action} sur {member.id}: {e}")

            verb = "expulsé" if action == "kick" else "banni"
            result = f"⚔️ **{success} {verb}(s)** ({failed} échec(s))"
            if skipped:
                result += f" — {skipped} membre(s) protégé(s) ignoré(s)"
            return result

        if action == "lockdown":
            try:
                everyone = guild.default_role
                locked = 0
                for ch in guild.text_channels:
                    try:
                        perms = ch.overwrites_for(everyone)
                        if perms.send_messages is not False:
                            perms.send_messages = False
                            await ch.set_permissions(everyone, overwrite=perms, reason="Anti-Raid: lockdown")
                            locked += 1
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                self._lockdown_active[guild.id] = True
                return f"🔒 **Lockdown activé** ({locked} salon(s) verrouillé(s)) — Utilise `/anti-raid action:lockdown-off` pour rétablir."
            except discord.Forbidden:
                return "⚠️ Permissions insuffisantes pour le lockdown."
            except discord.HTTPException as e:
                return f"⚠️ Erreur lockdown: {e}"

        return "Aucune action."

    @commands.Cog.listener()
    async def on_member_join(self, member: discord.Member):
        guild = member.guild
        if not guild or member.bot:
            return

        config = self._get_config(guild.id)
        if not has_premium(guild.id, 0, TIER_PREMIUM):
            return

        if not config.get("enabled"):
            return

        threshold = int(config.get("threshold", 5))
        window = int(config.get("window", 10))
        min_age = int(config.get("min_account_age_days", 7))

        now = datetime.now(timezone.utc).timestamp()
        suspicious = self._is_suspicious(member, min_age)

        joins = self._joins.setdefault(guild.id, deque())
        joins.append((now, member.id, suspicious))
        # Purger les entrées hors fenêtre
        while joins and (now - joins[0][0]) > window:
            joins.popleft()

        if len(joins) < threshold:
            return

        # Collecter les suspects (comptes récents) en excluant les membres protégés
        suspect_ids = [mid for _, mid, susp in joins if susp]
        suspects = []
        for mid in suspect_ids:
            m = guild.get_member(mid)
            if m and not _is_protected(m, guild):
                suspects.append(m)

        joins.clear()

        action_result = await self._trigger_action(guild, config, suspects)
        log_ch = await self._get_log_channel(guild, config)
        if log_ch:
            embed = discord.Embed(
                title="🚨 Anti-Raid déclenché",
                description=(
                    f"**{threshold}+ arrivées en {window}s détectées**\n\n"
                    f"Comptes suspects (< {min_age}j): **{len(suspects)}**\n"
                    f"Action: **{config['action']}**\n\n{action_result}"
                ),
                color=0xef4444,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Miaouscle Bot • Anti-Raid Premium")
            try:
                await log_ch.send(embed=embed)
            except discord.HTTPException:
                pass

        logger.warning(
            f"Anti-Raid déclenché sur {guild.name} ({guild.id}): "
            f"{len(suspects)} suspect(s) — action: {config['action']}"
        )

    async def _check_admin(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return False
        if not isinstance(interaction.user, discord.Member):
            return False
        if not interaction.user.guild_permissions.administrator and interaction.user.id not in OWNER_IDS:
            await interaction.response.send_message("❌ Tu dois être administrateur du serveur.", ephemeral=True)
            return False
        return True

    async def _send_status(self, interaction: discord.Interaction):
        config = self._get_config(interaction.guild.id)
        embed = discord.Embed(
            title="🛡️ Anti-Raid — Configuration",
            color=0x34d399 if config.get("enabled") else 0x6b7280,
        )
        embed.add_field(name="État", value="🟢 Activé" if config["enabled"] else "🔴 Désactivé", inline=True)
        embed.add_field(name="Action", value=f"`{config['action']}`", inline=True)
        embed.add_field(
            name="Détection",
            value=(
                f"**{config['threshold']}** arrivées en **{config['window']}s**\n"
                f"Âge mini compte sûr: **{config['min_account_age_days']}j**"
            ),
            inline=False,
        )
        if config.get("log_channel_id"):
            embed.add_field(name="Canal alerte", value=f"<#{config['log_channel_id']}>", inline=False)
        if self._lockdown_active.get(interaction.guild.id):
            embed.add_field(name="🔒 Lockdown", value="**ACTIF**", inline=False)
        embed.add_field(
            name="🛡️ Protection hiérarchie",
            value="Proprio, admins, modérateurs et membres avec rôle ≥ bot sont toujours ignorés.",
            inline=False,
        )
        embed.set_footer(text="Miaouscle Bot • Anti-Raid Premium")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="anti-raid", description="🛡️ Configurer l'Anti-Raid Premium (toutes les options)")
    @app_commands.describe(
        action="Action à effectuer",
        threshold="Nombre d'arrivées pour déclencher (2-50)",
        window="Fenêtre de détection en secondes (1-600)",
        min_account_age_days="Âge minimum d'un compte pour être considéré sûr (0-365 jours)",
        raid_action="Action à effectuer en cas de raid détecté",
        log_channel="Canal où envoyer les alertes Anti-Raid",
    )
    @app_commands.choices(action=[
        app_commands.Choice(name="📊 Voir le statut", value="status"),
        app_commands.Choice(name="🟢 Activer", value="enable"),
        app_commands.Choice(name="🔴 Désactiver", value="disable"),
        app_commands.Choice(name="⚙️ Configurer (utilise les options ci-dessous)", value="config"),
        app_commands.Choice(name="🔓 Lever le lockdown", value="lockdown-off"),
        app_commands.Choice(name="🧪 Tester la config", value="test"),
    ])
    @app_commands.choices(raid_action=[
        app_commands.Choice(name="🔔 Log uniquement", value="log"),
        app_commands.Choice(name="⚔️ Expulser les suspects", value="kick"),
        app_commands.Choice(name="🔨 Bannir les suspects", value="ban"),
        app_commands.Choice(name="🔒 Lockdown serveur", value="lockdown"),
    ])
    @premium_only(TIER_PREMIUM)
    async def anti_raid(
        self,
        interaction: discord.Interaction,
        action: app_commands.Choice[str],
        threshold: Optional[int] = None,
        window: Optional[int] = None,
        min_account_age_days: Optional[int] = None,
        raid_action: Optional[app_commands.Choice[str]] = None,
        log_channel: Optional[discord.TextChannel] = None,
    ):
        if not await self._check_admin(interaction):
            return

        guild_id = interaction.guild.id
        config = self._get_config(guild_id)

        if action.value == "status":
            await self._send_status(interaction)
            return

        if action.value == "enable":
            config["enabled"] = True
            self._save_config(guild_id, config)
            await interaction.response.send_message("✅ Anti-Raid **activé** sur ce serveur.", ephemeral=True)
            return

        if action.value == "disable":
            config["enabled"] = False
            self._save_config(guild_id, config)
            await interaction.response.send_message("🔴 Anti-Raid **désactivé**.", ephemeral=True)
            return

        if action.value == "config":
            changes = []
            if threshold is not None:
                if threshold < 2 or threshold > 50:
                    await interaction.response.send_message("❌ threshold doit être entre 2 et 50.", ephemeral=True)
                    return
                config["threshold"] = threshold
                changes.append(f"seuil → **{threshold}**")
            if window is not None:
                if window < 1 or window > 600:
                    await interaction.response.send_message("❌ window doit être entre 1 et 600s.", ephemeral=True)
                    return
                config["window"] = window
                changes.append(f"fenêtre → **{window}s**")
            if min_account_age_days is not None:
                if min_account_age_days < 0 or min_account_age_days > 365:
                    await interaction.response.send_message("❌ min_account_age_days entre 0 et 365.", ephemeral=True)
                    return
                config["min_account_age_days"] = min_account_age_days
                changes.append(f"âge mini → **{min_account_age_days}j**")
            if raid_action is not None:
                config["action"] = raid_action.value
                changes.append(f"action → **{raid_action.value}**")
            if log_channel is not None:
                config["log_channel_id"] = log_channel.id
                changes.append(f"canal → {log_channel.mention}")

            if not changes:
                await interaction.response.send_message(
                    "ℹ️ Aucune option fournie. Utilise `threshold`, `window`, `min_account_age_days`, `raid_action` ou `log_channel`.",
                    ephemeral=True,
                )
                return

            self._save_config(guild_id, config)
            await interaction.response.send_message(
                "✅ Configuration mise à jour:\n• " + "\n• ".join(changes),
                ephemeral=True,
            )
            return

        if action.value == "lockdown-off":
            if not self._lockdown_active.get(guild_id):
                await interaction.response.send_message("ℹ️ Aucun lockdown actif.", ephemeral=True)
                return
            await interaction.response.defer(ephemeral=True, thinking=True)
            try:
                everyone = interaction.guild.default_role
                unlocked = 0
                for ch in interaction.guild.text_channels:
                    try:
                        perms = ch.overwrites_for(everyone)
                        if perms.send_messages is False:
                            perms.send_messages = None
                            await ch.set_permissions(everyone, overwrite=perms, reason="Anti-Raid: fin lockdown")
                            unlocked += 1
                    except (discord.Forbidden, discord.HTTPException):
                        continue
                self._lockdown_active[guild_id] = False
                await interaction.followup.send(
                    f"🔓 Lockdown levé. **{unlocked}** salon(s) déverrouillé(s).",
                    ephemeral=True,
                )
            except discord.Forbidden:
                await interaction.followup.send("❌ Permissions insuffisantes.", ephemeral=True)
            except discord.HTTPException as e:
                await interaction.followup.send(f"❌ Erreur: {e}", ephemeral=True)
            return

        if action.value == "test":
            log_ch = await self._get_log_channel(interaction.guild, config)
            embed = discord.Embed(
                title="🧪 Test Anti-Raid",
                description=(
                    f"Config actuelle:\n"
                    f"• Activé: **{config['enabled']}**\n"
                    f"• Seuil: **{config['threshold']}** arrivées en **{config['window']}s**\n"
                    f"• Action: **{config['action']}**\n"
                    f"• Canal alerte: {log_ch.mention if log_ch else '⚠️ Aucun'}\n\n"
                    f"🛡️ **Membres toujours protégés:** proprio, admins, modérateurs, "
                    f"membres avec rôle ≥ bot.\n\n"
                    f"En cas de vrai raid, l'action **{config['action']}** serait exécutée "
                    f"uniquement sur les membres suspects non protégés."
                ),
                color=0xfbbf24,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(AntiRaidCog(bot))
