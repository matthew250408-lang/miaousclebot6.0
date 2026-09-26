"""Commandes /promo — Gestion des promotions tarifaires (propriétaire uniquement)."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

import discord
from discord import app_commands
from discord.ext import commands

from utils.payments import CATALOG, PLAN_PREMIUM, PLAN_PREMIUM_PLUS, BILLING_MONTHLY, BILLING_YEARLY
from utils.promo import get_promo, set_promo, clear_promo

logger = logging.getLogger(__name__)

OWNER_ID = 1140296215469240401

PLAN_LABELS = {
    PLAN_PREMIUM: "💎 Premium",
    PLAN_PREMIUM_PLUS: "👑 Premium+",
}
BILLING_LABELS = {
    BILLING_MONTHLY: "mensuel",
    BILLING_YEARLY: "annuel",
}


def _parse_date(date_str: str) -> datetime:
    """Parse DD/MM/YYYY HH:MM ou DD/MM HH:MM (année courante, heure UTC)."""
    s = date_str.strip()
    for fmt in ("%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    try:
        year = datetime.now(timezone.utc).year
        return datetime.strptime(f"{s} {year}", "%d/%m %H:%M %Y").replace(tzinfo=timezone.utc)
    except ValueError:
        pass
    raise ValueError(
        f"Format de date invalide : `{date_str}`\n"
        "Exemples valides : `30/06/2026 23:59` ou `30/06 23:59`"
    )


def _fmt_price(cents: int) -> str:
    return f"{cents / 100:.2f} €"


class PromoCog(commands.Cog):
    """Gestion des promos tarifaires — owner only."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    def _is_owner(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == OWNER_ID

    promo_group = app_commands.Group(
        name="promo",
        description="Gérer les promotions tarifaires (propriétaire uniquement)",
    )

    # -------------------------------------------------------------------------
    # /promo activer
    # -------------------------------------------------------------------------
    @promo_group.command(name="activer", description="Activer une promotion tarifaire avec compte à rebours")
    @app_commands.describe(
        plan="Plan concerné par la promo",
        billing="Fréquence de facturation concernée",
        prix="Nouveau prix en euros (ex : 1.99)",
        fin="Date/heure de fin en UTC (DD/MM/YYYY HH:MM ou DD/MM HH:MM)",
        label="Libellé affiché (ex : 🎉 Promo d'été)",
    )
    @app_commands.choices(
        plan=[
            app_commands.Choice(name="💎 Premium", value="premium"),
            app_commands.Choice(name="👑 Premium+", value="premium_plus"),
            app_commands.Choice(name="💎👑 Les deux", value="all"),
        ],
        billing=[
            app_commands.Choice(name="Mensuel", value="monthly"),
            app_commands.Choice(name="Annuel", value="yearly"),
            app_commands.Choice(name="Mensuel + Annuel", value="all"),
        ],
    )
    async def promo_activer(
        self,
        interaction: discord.Interaction,
        plan: app_commands.Choice[str],
        billing: app_commands.Choice[str],
        prix: str,
        fin: str,
        label: str = "🏷️ Promotion",
    ):
        if not self._is_owner(interaction):
            await interaction.response.send_message(
                "🔒 Cette commande est réservée au propriétaire du bot.", ephemeral=True
            )
            return

        try:
            promo_cents = int(round(float(prix.replace(",", ".")) * 100))
        except ValueError:
            await interaction.response.send_message(
                f"❌ Prix invalide : `{prix}`. Utilise un nombre comme `1.99`.", ephemeral=True
            )
            return

        try:
            end_dt = _parse_date(fin)
        except ValueError as exc:
            await interaction.response.send_message(f"❌ {exc}", ephemeral=True)
            return

        if end_dt <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ La date de fin doit être dans le futur.", ephemeral=True
            )
            return

        plans = [PLAN_PREMIUM, PLAN_PREMIUM_PLUS] if plan.value == "all" else [plan.value]
        billings = [BILLING_MONTHLY, BILLING_YEARLY] if billing.value == "all" else [billing.value]
        items = [
            {"plan": p, "billing": b, "promo_cents": promo_cents}
            for p in plans for b in billings
        ]

        set_promo(label=label, end_time=end_dt, items=items)

        end_ts = int(end_dt.timestamp())
        lines = []
        for item in items:
            orig_cents = CATALOG.get((item["plan"], item["billing"]), (0, ""))[0]
            lines.append(
                f"• {PLAN_LABELS.get(item['plan'], item['plan'])} {BILLING_LABELS.get(item['billing'], item['billing'])}: "
                f"~~{_fmt_price(orig_cents)}~~ → **{_fmt_price(promo_cents)}**"
            )

        embed = discord.Embed(title=f"✅ Promo activée — {label}", description="\n".join(lines), color=0xf39c12)
        embed.add_field(
            name="⏰ Fin de la promotion",
            value=f"<t:{end_ts}:F>\n⬇️ dans <t:{end_ts}:R>",
            inline=False,
        )
        embed.set_footer(text="La promo apparaîtra dans /premium et sur le dashboard.")
        await interaction.response.send_message(embed=embed, ephemeral=True)
        logger.info("Promo activée par %s : %s jusqu'à %s", interaction.user, label, end_dt)

    # -------------------------------------------------------------------------
    # /promo désactiver
    # -------------------------------------------------------------------------
    @promo_group.command(name="desactiver", description="Désactiver la promotion en cours")
    async def promo_desactiver(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message(
                "🔒 Cette commande est réservée au propriétaire du bot.", ephemeral=False
            )
            return
        promo = get_promo()
        if not promo:
            await interaction.response.send_message("ℹ️ Aucune promotion active.", ephemeral=False)
            return
        clear_promo()
        await interaction.response.send_message("✅ Promotion désactivée. Les prix normaux sont rétablis.", ephemeral=False)
        logger.info("Promo désactivée par %s", interaction.user)

    # -------------------------------------------------------------------------
    # /promo statut
    # -------------------------------------------------------------------------
    @promo_group.command(name="statut", description="Voir la promotion en cours et son compte à rebours")
    async def promo_statut(self, interaction: discord.Interaction):
        if not self._is_owner(interaction):
            await interaction.response.send_message(
                "🔒 Cette commande est réservée au propriétaire du bot.", ephemeral=False
            )
            return

        promo = get_promo()
        if not promo:
            await interaction.response.send_message("ℹ️ Aucune promotion active en ce moment.", ephemeral=False)
            return

        end_ts = None
        if promo.get("end_time"):
            try:
                end_ts = int(datetime.fromisoformat(promo["end_time"]).timestamp())
            except (ValueError, TypeError):
                pass

        lines = []
        for item in promo.get("items", []):
            orig_cents = CATALOG.get((item["plan"], item["billing"]), (0, ""))[0]
            lines.append(
                f"• {PLAN_LABELS.get(item['plan'], item['plan'])} {BILLING_LABELS.get(item['billing'], item['billing'])}: "
                f"~~{_fmt_price(orig_cents)}~~ → **{_fmt_price(item['promo_cents'])}**"
            )

        embed = discord.Embed(
            title=f"📊 Status Promotion — {promo.get('label', '')}",
            description="\n".join(lines) if lines else "Aucun article.",
            color=0xf39c12,
        )
        if end_ts:
            embed.add_field(
                name="⏰ Fin prévu le",
                value=f"<t:{end_ts}:F> ⏳",
                inline=False,
            )
        await interaction.response.send_message(embed=embed, ephemeral=False)


async def setup(bot: commands.Bot):
    await bot.add_cog(PromoCog(bot))
