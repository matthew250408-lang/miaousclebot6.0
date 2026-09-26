"""
Commandes admin pour gérer les abonnements Premium.
Réservées exclusivement au propriétaire principal du bot.

Groupe /premium-admin :
  grant-guild     — Accorder Premium à un serveur
  revoke-guild    — Révoquer Premium d'un serveur
  generate-code   — Générer un code cadeau/promo pour un utilisateur
  sync-roles      — Re-synchroniser les rôles Discord Premium (serveur support)
  check-roles     — Diagnostiquer les IDs de rôles Premium

Commande publique :
  /premium-status — Voir le statut Premium du serveur courant
"""
import logging
from datetime import datetime, timedelta, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.premium import (
    OWNER_IDS,
    TIER_PREMIUM,
    TIER_PREMIUM_PLUS,
    TIER_LABELS,
    grant_premium,
    revoke_premium,
    get_record,
    get_guild_tier,
    SUPPORT_GUILD_ID,
    PREMIUM_ROLE_ID,
    PREMIUM_PLUS_ROLE_ID,
)
from utils.premium_roles import sync_user_roles, sync_all_users
from utils.premium_codes import generate_code

logger = logging.getLogger(__name__)

PREMIUM_ADMIN_USER_ID = 1140296215469240401

TIER_CHOICES = [
    app_commands.Choice(name="💎 Premium", value=TIER_PREMIUM),
    app_commands.Choice(name="👑 Premium+", value=TIER_PREMIUM_PLUS),
]

DURATION_CHOICES = [
    app_commands.Choice(name="7 jours (essai)", value=7),
    app_commands.Choice(name="30 jours (1 mois)", value=30),
    app_commands.Choice(name="90 jours (3 mois)", value=90),
    app_commands.Choice(name="365 jours (1 an)", value=365),
    app_commands.Choice(name="À vie (lifetime)", value=0),
]


def _fmt_duration(days: int | None) -> str:
    if days is None or days <= 0:
        return "à vie"
    if days % 365 == 0:
        n = days // 365
        return f"{n} an" + ("s" if n > 1 else "")
    if days % 30 == 0:
        n = days // 30
        return f"{n} mois"
    return f"{days} jour" + ("s" if days > 1 else "")


def _format_record(record: dict | None, fallback_tier: str) -> str:
    if not record:
        return f"{TIER_LABELS.get(fallback_tier, fallback_tier)} (gratuit)"
    tier = record.get("tier", fallback_tier)
    label = TIER_LABELS.get(tier, tier)
    expires = record.get("expires")
    if expires:
        try:
            dt = datetime.fromisoformat(expires.replace("Z", "+00:00"))
            ts = int(dt.timestamp())
            return f"{label} • expire <t:{ts}:R>"
        except (ValueError, AttributeError):
            return f"{label} • expire {expires}"
    return f"{label} • à vie"


class PremiumAdminCog(commands.Cog):
    """Cog admin Premium : grant-guild, revoke-guild, generate-code, sync-roles, check-roles."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def _check_owner(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != PREMIUM_ADMIN_USER_ID:
            try:
                await interaction.response.send_message(
                    "🔒 Seul le propriétaire principal du bot peut gérer les abonnements Premium.",
                    ephemeral=True,
                )
            except (discord.NotFound, discord.HTTPException):
                pass
            return False
        return True

    @staticmethod
    async def _safe_send(interaction: discord.Interaction, **kwargs):
        try:
            if interaction.response.is_done():
                await interaction.followup.send(ephemeral=True, **kwargs)
            else:
                await interaction.response.send_message(ephemeral=True, **kwargs)
        except discord.NotFound:
            logger.warning("Interaction expirée — réponse Premium ignorée.")
        except discord.HTTPException as e:
            logger.warning(f"Erreur d'envoi de réponse Premium : {e}")

    premium_admin = app_commands.Group(
        name="premium-admin",
        description="Gérer les abonnements Premium (réservé aux owners)",
        default_permissions=discord.Permissions(administrator=True),
    )

    # ──────────────────────────────────────────────────────────────
    # grant-guild : accorder Premium à un serveur
    # ──────────────────────────────────────────────────────────────
    @premium_admin.command(name="grant-guild", description="Accorder Premium à un serveur entier")
    @app_commands.describe(
        guild_id="ID du serveur (vide = serveur actuel)",
        tier="Niveau d'abonnement",
        days="Durée en jours (vide = à vie)",
    )
    @app_commands.choices(tier=TIER_CHOICES)
    async def grant_guild(
        self,
        interaction: discord.Interaction,
        tier: app_commands.Choice[str],
        guild_id: str | None = None,
        days: int | None = None,
    ):
        if not await self._check_owner(interaction):
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        try:
            target_guild_id = int(guild_id) if guild_id else (interaction.guild.id if interaction.guild else None)
        except ValueError:
            await self._safe_send(interaction, content="❌ ID de serveur invalide.")
            return
        if not target_guild_id:
            await self._safe_send(interaction, content="❌ Aucun serveur cible spécifié.")
            return

        expires = datetime.now(timezone.utc) + timedelta(days=days) if days else None
        grant_premium(
            target_id=target_guild_id,
            target_type="guild",
            tier=tier.value,
            expires=expires,
            granted_by=interaction.user.id,
        )
        applied_tier = await sync_user_roles(self.bot, interaction.user.id)
        duration_text = f"pendant **{days} jours**" if days else "**à vie**"
        role_status = (
            f"\n🏷️ Rôle Discord mis à jour → **{TIER_LABELS.get(applied_tier, applied_tier)}**"
            if applied_tier else
            "\n⚠️ Rôle Discord non synchronisé (bot absent du serveur de support ou permission `Gérer les rôles` manquante)."
        )
        embed = discord.Embed(
            title="✅ Premium serveur accordé",
            description=(
                f"Le serveur `{target_guild_id}` a maintenant **{TIER_LABELS[tier.value]}** {duration_text}."
                f"{role_status}"
            ),
            color=0x34d399,
        )
        await self._safe_send(interaction, embed=embed)
        logger.info(f"Premium {tier.value} accordé au serveur {target_guild_id} par {interaction.user.id}")

    # ──────────────────────────────────────────────────────────────
    # revoke-guild : révoquer Premium d'un serveur
    # ──────────────────────────────────────────────────────────────
    @premium_admin.command(name="revoke-guild", description="Révoquer Premium d'un serveur")
    @app_commands.describe(guild_id="ID du serveur (vide = serveur actuel)")
    async def revoke_guild(self, interaction: discord.Interaction, guild_id: str | None = None):
        if not await self._check_owner(interaction):
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        try:
            target_guild_id = int(guild_id) if guild_id else (interaction.guild.id if interaction.guild else None)
        except ValueError:
            await self._safe_send(interaction, content="❌ ID de serveur invalide.")
            return
        if not target_guild_id:
            await self._safe_send(interaction, content="❌ Aucun serveur cible spécifié.")
            return

        existing_record = get_record(target_guild_id, "guild")
        former_sponsor_id = None
        if existing_record:
            gb = existing_record.get("granted_by")
            if gb:
                try:
                    former_sponsor_id = int(gb)
                except (TypeError, ValueError):
                    pass

        removed = revoke_premium(target_guild_id, target_type="guild")
        role_status = ""
        if removed and former_sponsor_id:
            new_tier = await sync_user_roles(self.bot, former_sponsor_id)
            if new_tier:
                role_status = f"\n🏷️ Rôle Discord du sponsor `{former_sponsor_id}` mis à jour → **{TIER_LABELS.get(new_tier, new_tier)}**."
        if removed:
            embed = discord.Embed(
                title="🗑️ Premium serveur révoqué",
                description=f"Le serveur `{target_guild_id}` n'est plus Premium.{role_status}",
                color=0xef4444,
            )
        else:
            embed = discord.Embed(
                title="ℹ️ Aucun Premium",
                description=f"Le serveur `{target_guild_id}` n'avait pas d'abonnement Premium actif.",
                color=0x6b7280,
            )
        await self._safe_send(interaction, embed=embed)

    # ──────────────────────────────────────────────────────────────
    # generate-code : générer un code cadeau/promo
    # ──────────────────────────────────────────────────────────────
    @premium_admin.command(
        name="generate-code",
        description="Générer un code Premium cadeau/promo pour un utilisateur",
    )
    @app_commands.describe(
        tier="Niveau Premium offert par ce code",
        duree="Durée du Premium accordé (à vie = lifetime)",
        destinataire="L'utilisateur qui verra le code via /code premium",
        valable_jours="Combien de jours le code reste valable sans utilisation (défaut : 90)",
        note="Note interne (raison, occasion…)",
    )
    @app_commands.choices(tier=TIER_CHOICES, duree=DURATION_CHOICES)
    async def generate_code_cmd(
        self,
        interaction: discord.Interaction,
        tier: app_commands.Choice[str],
        duree: app_commands.Choice[int],
        destinataire: discord.User,
        valable_jours: int = 90,
        note: str = "",
    ):
        if not await self._check_owner(interaction):
            return

        duration_days = duree.value if duree.value > 0 else None
        code = generate_code(
            tier=tier.value,
            duration_days=duration_days,
            created_by=interaction.user.id,
            recipient_user_id=destinataire.id,
            expires_in_days=valable_jours if valable_jours > 0 else None,
            note=note or None,
        )
        if not code:
            await interaction.response.send_message(
                "❌ Impossible de générer un code (DB indisponible ?).", ephemeral=True
            )
            return

        tier_label = TIER_LABELS.get(tier.value, tier.value)
        duration_label = _fmt_duration(duration_days)
        embed = discord.Embed(
            title="✅ Code Premium généré",
            description=(
                f"**Code** : `{code}`\n"
                f"**Tier** : {tier_label}\n"
                f"**Durée Premium** : {duration_label}\n"
                f"**Destinataire** : {destinataire.mention}\n"
                f"**Validité du code** : {valable_jours} jour(s)\n"
                + (f"**Note** : {note}\n" if note else "")
                + "\nLe destinataire peut désormais le voir via `/code premium`."
            ),
            color=0x10b981,
            timestamp=datetime.now(timezone.utc),
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

        try:
            dm_embed = discord.Embed(
                title="🎁 Tu as reçu un code Premium Miaouscle !",
                description=(
                    f"**Code** : `{code}`\n"
                    f"**Tier** : {tier_label}\n"
                    f"**Durée** : {duration_label}\n\n"
                    "Pour l'utiliser : `/code utiliser` sur un de tes serveurs."
                ),
                color=0xa855f7,
                timestamp=datetime.now(timezone.utc),
            )
            await destinataire.send(embed=dm_embed)
        except (discord.Forbidden, discord.HTTPException):
            pass

    # ──────────────────────────────────────────────────────────────
    # sync-roles : re-sync rôles Premium (serveur support)
    # ──────────────────────────────────────────────────────────────
    @premium_admin.command(
        name="sync-roles",
        description="Re-synchroniser les rôles Premium de tous les sponsors dans le serveur de support",
    )
    async def sync_roles(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return
        summary = await sync_all_users(self.bot)
        embed = discord.Embed(
            title="🔄 Sync des rôles Premium terminée",
            description=(
                f"**Total utilisateurs concernés** : {summary['total']}\n"
                f"**Synchronisés** : {summary['synced']}\n"
                f"**Ignorés** (pas membre du serveur de support) : {summary['skipped']}"
            ),
            color=0x60a5fa,
        )
        await self._safe_send(interaction, embed=embed)

    # ──────────────────────────────────────────────────────────────
    # check-roles : diagnostiquer les IDs de rôles
    # ──────────────────────────────────────────────────────────────
    @premium_admin.command(
        name="check-roles",
        description="Vérifie les IDs des rôles Premium dans le serveur de support",
    )
    async def check_roles(self, interaction: discord.Interaction):
        if not await self._check_owner(interaction):
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        support_guild = self.bot.get_guild(SUPPORT_GUILD_ID)
        if support_guild is None:
            await self._safe_send(
                interaction,
                content=f"❌ Serveur de support (`{SUPPORT_GUILD_ID}`) introuvable — le bot n'y est peut-être pas.",
            )
            return

        role_premium = support_guild.get_role(PREMIUM_ROLE_ID)
        role_premium_plus = support_guild.get_role(PREMIUM_PLUS_ROLE_ID)

        lines = [
            f"**Serveur de support :** `{support_guild.name}` (`{SUPPORT_GUILD_ID}`)\n",
            f"**Rôle Premium** (`{PREMIUM_ROLE_ID}`) : "
            + (f"✅ **{role_premium.name}**" if role_premium else "❌ **INTROUVABLE** — ID incorrect ou rôle supprimé"),
            f"**Rôle Premium+** (`{PREMIUM_PLUS_ROLE_ID}`) : "
            + (f"✅ **{role_premium_plus.name}**" if role_premium_plus else "❌ **INTROUVABLE** — ID incorrect ou rôle supprimé"),
        ]

        premium_like_roles = [r for r in support_guild.roles if "premium" in r.name.lower()]
        if premium_like_roles:
            lines.append("\n**Rôles contenant « premium » dans le serveur :**")
            for r in premium_like_roles:
                lines.append(f"• `{r.id}` — **{r.name}**")
        else:
            lines.append("\n⚠️ Aucun rôle avec « premium » dans le nom trouvé sur ce serveur.")

        if not role_premium or not role_premium_plus:
            lines.append("\n⚠️ Corrige les IDs dans `utils/premium.py` puis redémarre le bot.")
        else:
            lines.append("\n✅ Les deux rôles sont correctement configurés.")

        embed = discord.Embed(
            title="🔍 Diagnostic Rôles Premium",
            description="\n".join(lines),
            color=0x34d399 if (role_premium and role_premium_plus) else 0xef4444,
        )
        await self._safe_send(interaction, embed=embed)

    # ──────────────────────────────────────────────────────────────
    # /premium-status : commande publique (statut du serveur courant)
    # ──────────────────────────────────────────────────────────────
    @app_commands.command(name="premium-status", description="Voir le statut Premium de ce serveur")
    async def premium_status(self, interaction: discord.Interaction):
        if not interaction.guild:
            await self._safe_send(
                interaction,
                content="❌ Cette commande doit être utilisée dans un serveur.",
            )
            return
        try:
            await interaction.response.defer(ephemeral=True)
        except discord.NotFound:
            return

        guild_tier = get_guild_tier(interaction.guild.id)
        guild_record = get_record(interaction.guild.id, "guild")
        is_owner = interaction.user.id in OWNER_IDS

        embed = discord.Embed(
            title="💎 Statut Premium du serveur",
            color=0xfbbf24 if guild_tier != "free" else 0x6b7280,
        )
        embed.add_field(
            name=f"🏠 {interaction.guild.name}",
            value=_format_record(guild_record, guild_tier),
            inline=False,
        )
        if is_owner:
            embed.add_field(
                name="👑 Compte propriétaire du bot",
                value="Tu as accès aux commandes `/premium-admin`. Cela n'octroie pas de Premium au serveur.",
                inline=False,
            )

        if guild_tier == "free":
            embed.description = "Ce serveur n'a pas encore d'abonnement Premium. Active-le pour débloquer toutes les features avancées !"
            view = discord.ui.View()
            premium_url = "https://a7a54eed-3c9d-44cc-9bb5-f542e450c08d-00-7wjyd8rom8cz.janeway.replit.dev/premium"
            view.add_item(discord.ui.Button(label="Voir les tarifs", url=premium_url, emoji="💎"))
            await self._safe_send(interaction, embed=embed, view=view)
        else:
            await self._safe_send(interaction, embed=embed)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PremiumAdminCog(bot))
