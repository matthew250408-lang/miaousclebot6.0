"""
Cog : commandes Discord autour des codes Premium (côté utilisateur).

- /code premium    → voir les codes qui te sont destinés
- /code utiliser   → appliquer un code sur un serveur dont tu es admin

La génération de codes (réservée aux owners) se fait via :
  /premium-admin generate-code  (dans premium_admin.py)
"""
from __future__ import annotations

from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.premium import (
    TIER_LABELS,
    TIER_PREMIUM,
    TIER_PREMIUM_PLUS,
)
from utils.premium_codes import (
    list_codes_for_user,
    redeem_code,
)


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


def _fmt_dt(dt: datetime | None) -> str:
    if not dt:
        return "—"
    return discord.utils.format_dt(dt, style="R")


class PremiumCodesCog(commands.Cog):
    """Commandes /code pour les utilisateurs : voir et utiliser leurs codes Premium."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    code_group = app_commands.Group(
        name="code",
        description="Gérer tes codes Premium personnels",
    )

    # ──────────────────────────────────────────────────────────────
    # /code premium : voir ses codes
    # ──────────────────────────────────────────────────────────────
    @code_group.command(
        name="premium",
        description="Affiche les codes Premium qui te sont destinés",
    )
    async def code_premium(self, interaction: discord.Interaction):
        """Liste les codes Premium offerts à l'utilisateur (privé)."""
        codes = list_codes_for_user(interaction.user.id)

        if not codes:
            embed = discord.Embed(
                title="🎟️ Tes codes Premium",
                description="Tu n'as aucun code Premium en attente.\n\nSi tu en attends un, contacte le support.",
                color=0x6b7280,
                timestamp=datetime.now(timezone.utc),
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        embed = discord.Embed(
            title="🎟️ Tes codes Premium",
            description=(
                f"Tu as **{len(codes)} code(s) en attente**. Pour les utiliser :\n"
                "→ Choisis un serveur dont tu es **administrateur**\n"
                "→ Lance `/code utiliser code:… serveur:…`\n\n"
                "Les codes sont **privés** : ne les partage pas."
            ),
            color=0xa855f7,
            timestamp=datetime.now(timezone.utc),
        )
        for c in codes[:10]:
            tier_label = TIER_LABELS.get(c["tier"], c["tier"])
            duration = _fmt_duration(c.get("duration_days"))
            value_lines = [
                f"`{c['code']}`",
                f"**Tier** : {tier_label} • **Durée** : {duration}",
            ]
            if c.get("expires_at"):
                value_lines.append(f"⏳ Valable {_fmt_dt(c['expires_at'])}")
            if c.get("note"):
                value_lines.append(f"📝 *{c['note']}*")
            embed.add_field(
                name=f"{tier_label} — {duration}",
                value="\n".join(value_lines),
                inline=False,
            )
        if len(codes) > 10:
            embed.set_footer(text=f"+ {len(codes) - 10} autre(s) code(s) non affiché(s)")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ──────────────────────────────────────────────────────────────
    # /code utiliser : appliquer un code sur un serveur
    # ──────────────────────────────────────────────────────────────
    @code_group.command(
        name="utiliser",
        description="Appliquer un code Premium à un de tes serveurs",
    )
    @app_commands.describe(
        code="Le code reçu (format : MIA-XXXX-YYYY-ZZZZ)",
        serveur_id="ID du serveur cible (clic droit serveur → Copier l'ID en mode dev)",
    )
    async def code_utiliser(
        self,
        interaction: discord.Interaction,
        code: str,
        serveur_id: str,
    ):
        try:
            target_guild_id = int(serveur_id.strip())
        except ValueError:
            await interaction.response.send_message(
                "❌ L'ID du serveur doit être un nombre. Active le mode développeur "
                "Discord (Paramètres → Avancés), puis clic droit sur le serveur → "
                "« Copier l'ID ».",
                ephemeral=True,
            )
            return

        target_guild = self.bot.get_guild(target_guild_id)
        if target_guild is None:
            await interaction.response.send_message(
                f"❌ Le bot n'est pas présent sur le serveur `{target_guild_id}`. "
                "Invite-le d'abord avant d'appliquer ton code.",
                ephemeral=True,
            )
            return

        member = target_guild.get_member(interaction.user.id)
        if member is None:
            try:
                member = await target_guild.fetch_member(interaction.user.id)
            except discord.HTTPException:
                member = None
        if member is None or not member.guild_permissions.manage_guild:
            await interaction.response.send_message(
                f"❌ Tu dois avoir la permission **« Gérer le serveur »** sur "
                f"**{target_guild.name}** pour appliquer un code Premium.",
                ephemeral=True,
            )
            return

        result = redeem_code(code, interaction.user.id, target_guild_id)
        if not result["ok"]:
            messages = {
                "not_found":           "❌ Code introuvable. Vérifie l'orthographe (format : `MIA-XXXX-YYYY-ZZZZ`).",
                "already_redeemed":    "❌ Ce code a **déjà été utilisé**.",
                "expired":             "❌ Ce code est **expiré**.",
                "wrong_recipient":     "❌ Ce code est destiné à **un autre utilisateur**.",
                "service_unavailable": "❌ Service temporairement indisponible. Réessaie dans quelques minutes.",
                "internal_error":      "❌ Erreur interne. Contacte le support si le problème persiste.",
            }
            await interaction.response.send_message(
                messages.get(result["reason"], "❌ Échec de l'application du code."),
                ephemeral=True,
            )
            return

        tier_label = TIER_LABELS.get(result["tier"], result["tier"])
        expires = result.get("expires")
        when = f"jusqu'au {_fmt_dt(expires)}" if expires else "**à vie**"
        embed = discord.Embed(
            title="🎉 Code appliqué !",
            description=(
                f"**{target_guild.name}** est maintenant **{tier_label}** {when}.\n\n"
                "Tu reçois automatiquement le rôle correspondant dans le serveur "
                "de support Miaouscle si tu en es membre."
            ),
            color=0x10b981,
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text="Merci de soutenir Miaouscle 💜")
        await interaction.response.send_message(embed=embed, ephemeral=True)


async def setup(bot: commands.Bot):
    await bot.add_cog(PremiumCodesCog(bot))
