# cogs/general.py
"""
Module de commandes générales: ping, support, report, suggestion, help, mode-emploi.
- Commandes utilitaires multilingues
- Système d'aide avec navigation par catégories
- Manuel d'utilisation avec pagination
- Suggestion avec système d'approbation
"""
import discord
from discord.ext import commands
from discord import app_commands, Interaction, ui
from utils.translations import get_text
from utils.database import load_config
from utils.promo import get_promo, get_promo_price
from enum import Enum
import logging
from typing import Optional, List, Union
from datetime import datetime, timezone
import json
import os
import asyncio

logger = logging.getLogger(__name__)


def _read_json_sync(path, default):
    try:
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return default


async def _read_json_async(path, default):
    """Lecture JSON dans un thread worker pour ne pas bloquer l'event loop."""
    return await asyncio.to_thread(_read_json_sync, path, default)

# Configuration
OWNER_ID = 1140296215469240401
PRIORITY_IDS = {OWNER_ID, 1140296215469240401}

# Support URL
SUPPORT_URL = "https://discord.com/channels/1188506879052238988/1188578727668486184"

# Couleurs pour les embeds
EMBED_COLORS = {
    "ping": 0x00ff00,
    "support": 0x5865f2,
    "report": 0xff0000,
    "suggestion": 0xffd700,
    "help": 0x0099ff,
    "manual": {
        "p1": 0x00ff99,
        "p2": 0xffd700,
        "p3": 0xff6600,
        "p4": 0x00ccff,
        "p5": 0x9b59b6,
    }
}


# ============================================================================
# UI Views & Selects
# ============================================================================
class HelpSelect(ui.Select):
    """Select menu pour les catégories d'aide."""

    def __init__(self, guild_id: int):
        self.guild_id = guild_id
        options = [
            discord.SelectOption(
                label=get_text(guild_id, "general.help.levels_xp", default="📊 Leveling System"),
                value="levels",
                emoji="⭐",
            ),
            discord.SelectOption(
                label=get_text(guild_id, "general.help.moderation", default="Modération"),
                value="moderation",
                emoji="🛡️",
            ),
            discord.SelectOption(
                label=get_text(guild_id, "general.help.category_config", default="⚙️ Configuration"),
                value="config",
                emoji="⚙️",
            ),
            discord.SelectOption(
                label=get_text(guild_id, "general.help.category_general", default="🎮 General Commands"),
                value="general",
                emoji="🎮",
            ),
        ]
        super().__init__(
            placeholder=get_text(guild_id, "general.help.choose_category", default="Choose a category..."),
            options=options,
            min_values=1,
            max_values=1,
        )

    async def callback(self, interaction: Interaction) -> None:
        """Affiche l'aide pour la catégorie sélectionnée."""
        value = self.values[0]
        guild_id = self.guild_id

        embed = await self._create_category_embed(guild_id, value)
        embed.set_footer(text=get_text(guild_id, "general.manual.footer_prefix", default="Help Menu"))

        await interaction.response.edit_message(embed=embed)

    async def _create_category_embed(self, guild_id: int, category: str) -> discord.Embed:
        """Crée l'embed pour une catégorie."""
        if category == "levels":
            embed = discord.Embed(
                title=get_text(guild_id, "general.help.levels", default="📊 Leveling System"),
                description=get_text(guild_id, "general.help.levels_desc", default="Earn XP per message!"),
                color=EMBED_COLORS["help"],
            )
            embed.add_field(
                name="/niveau [@user]",
                value=get_text(guild_id, "general.help.level_command"),
                inline=False,
            )
            embed.add_field(
                name="/classement",
                value=get_text(guild_id, "general.help.leaderboard_command"),
                inline=False,
            )
            embed.add_field(
                name="/addxp @user XP",
                value=get_text(guild_id, "general.help.addxp_command"),
                inline=False,
            )
            embed.add_field(
                name="/removexp @user XP",
                value=get_text(guild_id, "general.help.removexp_command"),
                inline=False,
            )
            embed.add_field(
                name="/xp-journalier",
                value=get_text(guild_id, "general.help.daily_xp_command"),
                inline=False,
            )

        elif category == "moderation":
            embed = discord.Embed(
                title=get_text(guild_id, "general.help.moderation", default="Modération"),
                color=0xff6600,
            )
            embed.add_field(
                name="/warn @user raison",
                value=get_text(guild_id, "general.help.warn_command"),
                inline=False,
            )
            embed.add_field(
                name="/mute @user minutes",
                value=get_text(guild_id, "general.help.mute_command"),
                inline=False,
            )
            embed.add_field(
                name="/unmute @user",
                value=get_text(guild_id, "general.help.unmute_command"),
                inline=False,
            )
            embed.add_field(
                name="/kick @user raison",
                value=get_text(guild_id, "general.help.kick_command"),
                inline=False,
            )
            embed.add_field(
                name="/ban @user raison",
                value=get_text(guild_id, "general.help.ban_command"),
                inline=False,
            )
            embed.add_field(
                name="/unban USER_ID",
                value=get_text(guild_id, "general.help.unban_command"),
                inline=False,
            )

        elif category == "config":
            embed = discord.Embed(
                title=get_text(guild_id, "general.help.category_config", default="⚙️ Configuration"),
                color=0x00ccff,
            )
            embed.add_field(
                name="/config-serv",
                value=get_text(guild_id, "general.help.config_command"),
                inline=False,
            )
            embed.add_field(
                name="/changer-pseudo pseudo",
                value=get_text(guild_id, "general.help.changenick_command"),
                inline=False,
            )

        else:  # general
            embed = discord.Embed(
                title=get_text(guild_id, "general.help.category_general", default="🎮 General Commands"),
                color=0x0099ff,
            )
            embed.add_field(
                name="/help",
                value=get_text(guild_id, "general.help.help_command"),
                inline=False,
            )
            embed.add_field(
                name="/ping",
                value=get_text(guild_id, "general.help.ping_command"),
                inline=False,
            )
            embed.add_field(
                name="/support",
                value=get_text(guild_id, "general.help.support_command"),
                inline=False,
            )
            embed.add_field(
                name="/report bug",
                value=get_text(guild_id, "general.help.report_command"),
                inline=False,
            )

        return embed


class HelpView(ui.View):
    """View pour le menu d'aide."""

    def __init__(self, guild_id: int):
        super().__init__(timeout=300)
        self.add_item(HelpSelect(guild_id))


class ModeEmploiView(ui.View):
    """View pour la pagination du mode d'emploi."""

    def __init__(self, pages: List[discord.Embed]):
        super().__init__(timeout=300)
        self.pages = pages
        self.current_page = 0

    @ui.button(label="◀️", style=discord.ButtonStyle.gray)
    async def previous_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Bouton page précédente."""
        if self.current_page > 0:
            self.current_page -= 1
            await self._update_view(interaction)
        else:
            await interaction.response.defer()

    @ui.button(label="1️⃣", style=discord.ButtonStyle.primary)
    async def page_1(self, interaction: Interaction, button: ui.Button) -> None:
        """Aller à la page 1."""
        self.current_page = 0
        await self._update_view(interaction)

    @ui.button(label="2️⃣", style=discord.ButtonStyle.primary)
    async def page_2(self, interaction: Interaction, button: ui.Button) -> None:
        """Aller à la page 2."""
        self.current_page = 1
        await self._update_view(interaction)

    @ui.button(label="3️⃣", style=discord.ButtonStyle.primary)
    async def page_3(self, interaction: Interaction, button: ui.Button) -> None:
        """Aller à la page 3."""
        self.current_page = 2
        await self._update_view(interaction)

    @ui.button(label="4️⃣", style=discord.ButtonStyle.primary)
    async def page_4(self, interaction: Interaction, button: ui.Button) -> None:
        """Aller à la page 4."""
        self.current_page = 3
        await self._update_view(interaction)

    @ui.button(label="5️⃣", style=discord.ButtonStyle.primary)
    async def page_5(self, interaction: Interaction, button: ui.Button) -> None:
        """Aller à la page 5."""
        self.current_page = 4
        await self._update_view(interaction)

    @ui.button(label="▶️", style=discord.ButtonStyle.gray)
    async def next_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Bouton page suivante."""
        if self.current_page < len(self.pages) - 1:
            self.current_page += 1
            await self._update_view(interaction)
        else:
            await interaction.response.defer()

    async def _update_view(self, interaction: Interaction) -> None:
        """Met à jour l'affichage de la page."""
        self._update_buttons()
        await interaction.response.edit_message(embed=self.pages[self.current_page], view=self)

    def _update_buttons(self) -> None:
        """Active/désactive les boutons selon la page actuelle."""
        self.previous_button.disabled = (self.current_page == 0)
        self.next_button.disabled = (self.current_page == len(self.pages) - 1)


class SuggestionView(ui.View):
    """View pour l'approbation/rejet de suggestions."""

    def __init__(self, bot: commands.Bot, author: Union[discord.User, discord.Member]):
        super().__init__(timeout=None)
        self.bot = bot
        self.author = author
        self.message: Optional[discord.Message] = None

    @ui.button(label="✅", style=discord.ButtonStyle.success)
    async def accept_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Accepte une suggestion."""
        if interaction.user.id not in PRIORITY_IDS:
            guild_id = interaction.guild.id if interaction.guild else 0
            await interaction.response.send_message(
                get_text(guild_id, "general.help.permission_denied", default="Permission denied"),
                ephemeral=True,
            )
            return

        if self.message and self.message.embeds:
            embed = self.message.embeds[0]
            embed.color = discord.Color.green()
            embed.title = get_text(0, "general.suggestion.accepted_title", default="✅ Suggestion accepted")
            await self.message.edit(embed=embed, view=None)
            await interaction.response.defer()

    @ui.button(label="❌", style=discord.ButtonStyle.danger)
    async def reject_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Rejette une suggestion."""
        if interaction.user.id not in PRIORITY_IDS:
            guild_id = interaction.guild.id if interaction.guild else 0
            await interaction.response.send_message(
                get_text(guild_id, "general.help.permission_denied", default="Permission denied"),
                ephemeral=True,
            )
            return

        if self.message and self.message.embeds:
            embed = self.message.embeds[0]
            embed.color = discord.Color.red()
            embed.title = get_text(0, "general.suggestion.refused_title", default="❌ Suggestion refused")
            await self.message.edit(embed=embed, view=None)
            await interaction.response.defer()


# ============================================================================
# General Cog
# ============================================================================
class General(commands.Cog):
    """Cog de commandes générales: ping, support, report, suggestion, help, mode-emploi."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ========================================================================
    # Helper Methods
    # ========================================================================
    async def _check_guild(self, interaction: Interaction) -> bool:
        """Vérifie que la commande est utilisée sur un serveur."""
        if not interaction.guild:
            try:
                await interaction.response.send_message(
                    get_text(0, "common.dm_channel_only", default="This command only works on a server."),
                    ephemeral=True,
                )
            except:
                pass
            return False
        return True

    async def _validate_text_length(
        self,
        interaction: Interaction,
        text: str,
        max_length: int = 500,
    ) -> bool:
        """Valide la longueur d'un texte."""
        if len(text) > max_length:
            guild_id = interaction.guild.id if interaction.guild else 0
            await interaction.response.send_message(
                get_text(guild_id, "general.report.limit_error", default="❌ Text cannot exceed 500 characters."),
                ephemeral=True,
            )
            return False
        return True

    # ========================================================================
    # Commands
    # ========================================================================
    @app_commands.command(
        name="ping",
        description="Vérifier la latence du bot",
    )
    async def ping(self, interaction: Interaction) -> None:
        """Affiche la latence du bot."""
        latency = round(self.bot.latency * 1000)
        embed = discord.Embed(
            title="🏓 Pong !",
            description=f"Ma latence est de **{latency}ms**",
            color=0x00ff00
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="help",
        description="Afficher l'aide et toutes les commandes disponibles",
    )
    async def help(self, interaction: Interaction) -> None:
        """Affiche le menu d'aide avec navigation par catégories."""
        guild_id = interaction.guild.id if interaction.guild else 0
        embed = discord.Embed(
            title="📚 Aide — Miaouscle Bot",
            description="Toutes les commandes disponibles. Utilisez le menu ci-dessous pour naviguer par catégorie.",
            color=EMBED_COLORS["help"],
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="⭐ Niveaux & XP",
            value="`/niveau` — voir son niveau\n`/classement` — top 10 du serveur\n`/ajouter-xp` — ajouter de l'XP à un membre",
            inline=False
        )
        embed.add_field(
            name="🛡️ Modération",
            value="`/warn` — avertir un membre\n`/mute` `/unmute` — silence temporaire\n`/ban` `/unban` — bannissement\n`/kick` — expulsion\n`/sanction-liste` — voir les sanctions d'un membre",
            inline=False
        )
        embed.add_field(
            name="⚙️ Configuration & Admin",
            value="`/config` — voir & modifier la config\n`/panel` — accéder au dashboard web\n`/maintenance` — mode maintenance on/off\n`/donner-role` — attribuer un rôle",
            inline=False
        )
        embed.add_field(
            name="🎮 Général & Info",
            value="`/ping` — latence du bot\n`/status` — statut du bot & site + maintenances\n`/support` — serveur de support\n`/report` — signaler un problème\n`/suggestion` — proposer une idée\n`/info bot|serveur|role|user` — informations détaillées\n`/mode-emploi` — manuel d'utilisation\n`/modification-recentes` — dernières mises à jour",
            inline=False
        )
        embed.set_footer(text="Miaouscle Bot v12.8.0 • /panel pour le dashboard web")
        view = discord.ui.View()
        view.add_item(HelpSelect(guild_id))
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(
        name="support",
        description="Obtenir le lien du serveur de support",
    )
    async def support(self, interaction: Interaction) -> None:
        """Affiche le lien du serveur de support."""
        embed = discord.Embed(
            title="🆘 Besoin d'aide ?",
            description=f"Rejoignez notre serveur de support pour obtenir de l'aide :\n{SUPPORT_URL}",
            color=0x5865f2
        )
        await interaction.response.send_message(embed=embed)

    @app_commands.command(
        name="mode-emploi",
        description="Consulter le manuel d'utilisation complet",
    )
    async def mode_emploi(self, interaction: Interaction) -> None:
        """Affiche le manuel d'utilisation avec pagination."""
        guild_id = interaction.guild.id if interaction.guild else 0
        pages = await self._create_manual_pages(guild_id)
        await interaction.response.send_message(embed=pages[0], view=ModeEmploiView(pages))

    @app_commands.command(
        name="report",
        description="Signaler un problème au staff",
    )
    @app_commands.describe(raison="Description détaillée du problème")
    async def report(self, interaction: Interaction, raison: str) -> None:
        """Permet de signaler un problème avec un embed premium."""
        if not await self._check_guild(interaction):
            return

        if not await self._validate_text_length(interaction, raison):
            return

        channel = self.bot.get_channel(1332455504777318561)
        if channel:
            embed = discord.Embed(
                title="🚨 Nouveau Report",
                color=0xff0000,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="👤 Utilisateur", value=f"{interaction.user.mention}\n`{interaction.user.id}`", inline=True)
            embed.add_field(name="🏠 Serveur", value=f"**{interaction.guild.name}**\n`{interaction.guild.id}`", inline=True)
            embed.add_field(name="💬 Message", value=f"```\n{raison}\n```", inline=False)
            embed.set_footer(text="Miaouscle Bot Feedback System")

            await channel.send(embed=embed)

            confirm_embed = discord.Embed(
                title="✅ Report Envoyé avec succès !",
                description="Votre signalement a bien été transmis au développeur.",
                color=0x7c3aed
            )
            confirm_embed.add_field(name="Contenu envoyé :", value=f"*{raison}*", inline=False)
            confirm_embed.set_footer(text="Merci pour votre retour !")

            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

    @app_commands.command(
        name="suggestion",
        description="Soumettre une idée d'amélioration",
    )
    @app_commands.describe(idee="Votre idée pour améliorer le bot")
    async def suggestion(self, interaction: Interaction, idee: str) -> None:
        """Permet de soumettre une suggestion avec un embed premium."""
        if not await self._check_guild(interaction):
            return

        if not await self._validate_text_length(interaction, idee):
            return

        channel = self.bot.get_channel(1344414298847576104)
        if channel:
            embed = discord.Embed(
                title="💡 Nouvelle Suggestion",
                color=0xffd700,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_author(name=interaction.user.name, icon_url=interaction.user.display_avatar.url)
            embed.add_field(name="👤 Proposé par", value=f"{interaction.user.mention}", inline=True)
            embed.add_field(name="🏠 Serveur", value=f"{interaction.guild.name}", inline=True)
            embed.add_field(name="📝 Idée", value=f"```\n{idee}\n```", inline=False)
            embed.set_footer(text="Système de Suggestion Miaouscle Bot")

            msg = await channel.send(embed=embed)
            await msg.add_reaction("✅")
            await msg.add_reaction("❌")

            confirm_embed = discord.Embed(
                title="✅ Suggestion Enregistrée",
                description="Merci pour votre proposition ! Elle est maintenant visible par les développeurs.",
                color=0x7c3aed
            )
            confirm_embed.add_field(name="Votre idée :", value=f"*{idee}*", inline=False)

            await interaction.response.send_message(embed=confirm_embed, ephemeral=True)

    @app_commands.command(name="status", description="Voir le statut du bot et du site web")
    async def status(self, interaction: discord.Interaction):
        """Affiche le statut en temps réel du bot et du site, ainsi que les maintenances à venir."""
        try:
            await interaction.response.defer()
        except discord.NotFound:
            return

        # Charger maintenance.json (async pour ne pas bloquer l'event loop)
        maint_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data', 'maintenance.json')
        history_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), 'web', 'data', 'maintenance_history.json')

        maint_data = await _read_json_async(maint_path, {})
        history = await _read_json_async(history_path, [])
        if not isinstance(history, list):
            history = []

        bot_data = maint_data.get('bot', {}) if isinstance(maint_data, dict) else {}
        site_data = maint_data.get('site', {}) if isinstance(maint_data, dict) else {}

        bot_maintenance = bot_data.get('active', False)
        site_maintenance = site_data.get('active', False)

        def _ts(iso: str | None) -> str | None:
            """Convertit une ISO string en timestamp Discord ou None."""
            if not iso:
                return None
            try:
                dt = datetime.fromisoformat(iso.replace('Z', '+00:00'))
                return str(int(dt.timestamp()))
            except Exception:
                return None

        def _maint_detail(data: dict) -> str:
            """Construit les lignes début/fin pour un bloc de maintenance actif."""
            lines = []
            start_ts = _ts(data.get('start_time'))
            end_ts = _ts(data.get('end_time'))
            if start_ts:
                lines.append(f"   ↳ Début : <t:{start_ts}:F>")
            if end_ts:
                lines.append(f"   ↳ Fin prévue : <t:{end_ts}:F> (<t:{end_ts}:R>)")
            return "\n".join(lines)

        # Statut bot
        bot_latency = round(self.bot.latency * 1000)
        if bot_maintenance:
            bot_status = "🟡 En maintenance"
            detail = _maint_detail(bot_data)
            if detail:
                bot_status += "\n" + detail
        else:
            bot_status = f"🟢 Opérationnel ({bot_latency} ms)"

        # Statut site
        if site_maintenance:
            site_status = "🟡 En maintenance"
            detail = _maint_detail(site_data)
            if detail:
                site_status += "\n" + detail
        else:
            site_status = "🟢 Opérationnel"

        # Couleur globale de l'embed
        embed_color = 0xf39c12 if (bot_maintenance or site_maintenance) else 0x27ae60

        embed = discord.Embed(
            title="📡 Statut — Miaouscle Bot",
            color=embed_color,
            timestamp=datetime.now(timezone.utc)
        )

        embed.add_field(name="🤖 Bot Discord", value=bot_status, inline=True)
        embed.add_field(name="🌐 Site Web", value=site_status, inline=True)

        # Maintenances programmées (futures) — affiche chacune avec son heure
        now = datetime.now(timezone.utc)
        scheduled_lines = []
        for item in history:
            if item.get('status') == 'scheduled':
                sched_ts = _ts(item.get('scheduled_time'))
                end_ts = _ts(item.get('end_time'))
                if sched_ts:
                    try:
                        sched_dt = datetime.fromisoformat(
                            item['scheduled_time'].replace('Z', '+00:00')
                        )
                        if sched_dt <= now:
                            continue
                    except Exception:
                        pass
                    line = f"• <t:{sched_ts}:F>"
                    if end_ts:
                        line += f" → <t:{end_ts}:t>"
                    scheduled_lines.append(line)

        if scheduled_lines:
            maint_api_url = "https://2324b015-c8f1-4ddd-8e70-b4b7c62aa019-00-1hxhid5mmj7a4.kirk.replit.dev/maintenance-annonce"
            embed.add_field(
                name="📅 Maintenances programmées",
                value="\n".join(scheduled_lines[:5]) + f"\n\n[🔗 Voir toutes les maintenances prévues]({maint_api_url})",
                inline=False
            )

        status_url = "https://5fb0aa6a-805b-4226-8920-840f0456aaa6-00-2bv8yu9qde71l.picard.replit.dev/status"
        embed.set_footer(text="Miaouscle Bot • Mis à jour")
        view = discord.ui.View()
        view.add_item(discord.ui.Button(label="Voir le statut complet", url=status_url, emoji="🌐"))
        await interaction.followup.send(embed=embed, view=view)

    @app_commands.command(name="panel", description="Accéder au dashboard web")
    async def panel(self, interaction: discord.Interaction):
        """Accès direct au dashboard."""
        url = "https://5fb0aa6a-805b-4226-8920-840f0456aaa6-00-2bv8yu9qde71l.picard.replit.dev/home"
        if interaction.guild:
            url = f"https://26439f25-759a-4aef-88be-0110cb82a6f6-00-142vrfb9pgave.picard.replit.dev/dashboard{interaction.guild.id}"
        embed = discord.Embed(
            title="🎯 Dashboard Miaouscle Bot",
            description=f"Gérez votre serveur via le panel web.\n\n🔗 [Accéder au Panel]({url})",
            color=0x7c3aed
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="premium", description="Découvrir Miaouscle Premium et ses avantages")
    async def premium(self, interaction: discord.Interaction):
        """Aperçu des plans Premium, prix, et lien vers la boutique."""
        promo = get_promo()

        def _price_line(plan: str, billing: str, normal_eur: str) -> str:
            """Retourne la ligne de prix : promo si active, sinon prix normal."""
            promo_cents = get_promo_price(plan, billing) if promo else None
            if promo_cents is not None:
                return f"~~{normal_eur}~~ → **{promo_cents / 100:.2f} €** / mois 🏷️"
            return f"**{normal_eur}** / mois"

        p_monthly = _price_line("premium", "monthly", "2,99 €")
        pp_monthly = _price_line("premium_plus", "monthly", "5,99 €")

        color = 0xf39c12 if promo else 0xa855f7

        embed = discord.Embed(
            title="✨ Miaouscle Premium — Maintenant disponible !",
            description=(
                "Trois forfaits, une seule promesse : **un Discord plus vivant, "
                "mieux modéré, plus personnalisé.**\n\n"
                "Comparez les plans ci-dessous — la principale différence entre "
                "Premium et Premium+ est le **nombre de serveurs couverts** par "
                "votre abonnement."
            ),
            color=color,
            timestamp=datetime.now(timezone.utc),
        )

        # Bannière promo avec compte à rebours
        if promo:
            end_ts = None
            if promo.get("end_time"):
                try:
                    from datetime import datetime as _dt
                    end_ts = int(_dt.fromisoformat(promo["end_time"]).timestamp())
                except (ValueError, TypeError):
                    pass
            countdown = f"\n⏰ Se termine <t:{end_ts}:R> !" if end_ts else ""
            embed.add_field(
                name=f"🏷️ {promo.get('label', 'Promotion en cours')}",
                value=f"Prix réduits pour une durée limitée !{countdown}",
                inline=False,
            )

        embed.add_field(
            name="Gratuit",
            value=(
                "**0 €** / mois\n"
                "*Pour bien démarrer*\n"
                "\u200b\n"
                "• XP & niveaux\n"
                "• Modération de base\n"
                "• Dashboard web\n"
                "• Tickets simples"
            ),
            inline=True,
        )

        embed.add_field(
            name="💎 Premium",
            value=(
                f"{p_monthly}\n"
                "*Pour une communauté active*\n"
                "\u200b\n"
                "**1 serveur** couvert\n"
                "• Tout le Gratuit\n"
                "• Cartes de niveau personnalisées\n"
                "• Bienvenue avec image\n"
                "• Auto-modération avancée\n"
                "• Support sous 24 h"
            ),
            inline=True,
        )

        embed.add_field(
            name="👑 Premium+",
            value=(
                f"{pp_monthly}\n"
                "*Pour les multi-serveurs*\n"
                "\u200b\n"
                "**Jusqu'à 3 serveurs** couverts\n"
                "• Tout le Premium\n"
                "• Tickets pro & sondages\n"
                "• Rappels & événements\n"
                "• Stats avancées + export\n"
                "• Support VIP sous 6 h"
            ),
            inline=True,
        )

        embed.add_field(
            name="\u200b",
            value=(
                "✅ **Les abonnements sont ouverts !**\n"
                "Paiement sécurisé par **Stripe** (carte bancaire) ou **PayPal**.\n"
                "Annulable à tout moment depuis le dashboard.\n\n"
                "💡 Tu peux aussi utiliser un code offert par l'équipe "
                "avec `/code utiliser`."
            ),
            inline=False,
        )

        embed.set_footer(text="Tarif annuel : -2 mois offerts • Annulable à tout moment")
        embed.set_thumbnail(url=interaction.client.user.display_avatar.url if interaction.client.user else None)

        view = discord.ui.View()
        premium_url = "https://5fb0aa6a-805b-4226-8920-840f0456aaa6-00-2bv8yu9qde71l.picard.replit.dev/premium"
        view.add_item(discord.ui.Button(label="🛒 Souscrire maintenant", url=premium_url, style=discord.ButtonStyle.link))
        view.add_item(discord.ui.Button(label="Serveur de support", url=SUPPORT_URL, style=discord.ButtonStyle.link))
        await interaction.response.send_message(embed=embed, view=view)

    @app_commands.command(name="modification-recentes", description="Voir les dernières mises à jour du bot")
    async def modification_recentes(self, interaction: discord.Interaction):
        """Affiche les nouveautés v12.9.0."""
        embed = discord.Embed(
            title="✨ Miaouscle Bot — Mises à jour",
            description="Voici les derniers changements apportés au bot et au dashboard.",
            color=0x7c3aed,
            timestamp=datetime.now(timezone.utc)
        )
        embed.add_field(
            name="💎 v12.12.0 — 25 Avr 2026",
            value=(
                "• **[SITE]** Statut **Premium visible sur le dashboard** (sidebar + bannière + badges par serveur)\n"
                "• **[SITE]** Nouvelle API `/api/user/premium` (statut Premium des serveurs admin)\n"
                "• **[BOT]** Le serveur sélectionné bénéficie d'un **Premium+ offert gratuitement à vie**"
            ),
            inline=False
        )
        embed.add_field(
            name="🎁 v12.11.0 — 25 Avr 2026",
            value=(
                "• **[BOT]** `/anti-raid` simplifiée: **une seule commande** avec toutes les options\n"
                "• **[BOT]** Nouvelle `/embed-custom` (Premium): embeds personnalisés\n"
                "• **[BOT]** Nouvelle `/rappel` (Premium+): rappels programmés\n"
                "• **[BOT]** Nouvelle `/sondage` (Premium+): sondage multi-options"
            ),
            inline=False
        )
        embed.add_field(
            name="🛡️ v12.10.0 — 25 Avr 2026",
            value=(
                "• **[BOT]** Module **Anti-Raid Premium** avec détection auto + lockdown\n"
                "• **[BOT]** Système Premium central, `/premium-admin`, `/premium-status`"
            ),
            inline=False
        )
        embed.set_footer(text="Miaouscle Bot v13.0.3 • Utilisez /panel pour le dashboard")
        view = discord.ui.View()
        dashboard_url = "https://5fb0aa6a-805b-4226-8920-840f0456aaa6-00-2bv8yu9qde71l.picard.replit.dev/home"
        premium_url = "https://5fb0aa6a-805b-4226-8920-840f0456aaa6-00-2bv8yu9qde71l.picard.replit.dev/premium"
        view.add_item(discord.ui.Button(label="Voir le Dashboard", url=dashboard_url, emoji="🌐"))
        view.add_item(discord.ui.Button(label="Premium", url=premium_url, emoji="💎"))
        await interaction.response.send_message(embed=embed, view=view)

    # Info commands as group
    info_group = app_commands.Group(name="info", description="Informations sur le bot, serveur, rôles, utilisateurs")

    @info_group.command(name="bot", description="Afficher les infos du bot")
    async def info_bot(self, interaction: Interaction) -> None:
        """Affiche les informations du bot."""
        if not await self._check_guild(interaction):
            return

        guild_id = interaction.guild.id if interaction.guild else 0
        bot_name = self.bot.user.name if self.bot.user else "Miaouscle Bot"
        total_users = sum(g.member_count for g in self.bot.guilds if g.member_count)

        embed = discord.Embed(
            title=f"🐱 {get_text(guild_id, 'general.info.bot_name', default='Bot Name')}: {bot_name}",
            color=0x5865f2,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="📌 ID", value=str(self.bot.user.id) if self.bot.user else "N/A", inline=True)
        embed.add_field(name="📦 Version", value="inconnu", inline=True)
        embed.add_field(name="⏱️ Ping", value=f"{round(self.bot.latency * 1000)}ms", inline=True)
        embed.add_field(name="🖥️ Serveurs", value=str(len(self.bot.guilds)), inline=True)
        embed.add_field(name="👥 Utilisateurs totaux", value=str(total_users), inline=True)
        embed.add_field(name="💬 Commandes", value="28 slash commands", inline=True)
        embed.add_field(name="🌍 Langues", value="FR • EN • ES • DE", inline=False)
        embed.add_field(name="✨ Fonctionnalités", value="🎮 Leveling • 🛡️ Modération • ⚙️ Configuration • 📊 Statistiques", inline=False)
        if self.bot.user and self.bot.user.avatar:
            embed.set_thumbnail(url=self.bot.user.avatar.url)
        await interaction.response.defer(); await interaction.followup.send(embed=embed)

    @info_group.command(name="serveur", description="Afficher les infos du serveur")
    async def info_serveur(self, interaction: Interaction) -> None:
        """Affiche les informations du serveur."""
        if not interaction.guild:
            await interaction.response.send_message("❌ Cette commande ne fonctionne que sur un serveur", ephemeral=True)
            return

        guild = interaction.guild
        guild_id = guild.id

        # Calculer les statistiques
        bots = sum(1 for m in guild.members if m.bot)
        humans = guild.member_count - bots if guild.member_count else 0
        text_channels = len([c for c in guild.channels if isinstance(c, discord.TextChannel)])
        voice_channels = len([c for c in guild.channels if isinstance(c, discord.VoiceChannel)])
        days_old = (discord.utils.utcnow() - guild.created_at).days

        embed = discord.Embed(
            title=f"🏰 {guild.name}",
            description=f"Créé le {discord.utils.format_dt(guild.created_at, style='d')} ({days_old} jours)",
            color=0x2bbb40,
        )

        # Section: Identité
        embed.add_field(
            name="📌 Identité",
            value=f"**ID:** `{guild.id}`\n**Propriétaire:** {guild.owner.mention if guild.owner else 'Inconnu'}",
            inline=False
        )

        # Section: Membres
        embed.add_field(
            name="👥 Membres",
            value=f"**Total:** {guild.member_count}\n**Humains:** {humans}\n**Bots:** {bots}",
            inline=False
        )

        # Section: Salons
        embed.add_field(
            name="🔤 Salons",
            value=f"**Texte:** {text_channels}\n**Vocaux:** {voice_channels}",
            inline=False
        )

        # Section: Organisation
        embed.add_field(
            name="🎭 Organisation",
            value=f"**Rôles:** {len(guild.roles)}\n**Emojis:** {len(guild.emojis)}",
            inline=False
        )

        # Section: Premium
        boost_level = guild.premium_tier
        boosts = guild.premium_subscription_count
        embed.add_field(
            name="🚀 Niveau Premium",
            value=f"**Tier:** Niveau {boost_level}\n**Boosts:** {boosts}",
            inline=False
        )

        if guild.icon:
            embed.set_thumbnail(url=guild.icon.url)
        if guild.banner:
            embed.set_image(url=guild.banner.url)
        await interaction.response.defer(); await interaction.followup.send(embed=embed)

    @info_group.command(name="role", description="Afficher les infos d'un rôle")
    @app_commands.describe(role="Le rôle à afficher")
    async def info_role(self, interaction: Interaction, role: discord.Role) -> None:
        """Affiche les informations d'un rôle."""
        if not interaction.guild:
            return

        guild_id = interaction.guild.id
        members_with_role = len([m for m in interaction.guild.members if role in m.roles])
        total_members = len(interaction.guild.members)

        embed = discord.Embed(
            color=role.color if role.color != discord.Color.default() else 0x5865f2,
        )

        # Titre avec icône de rôle
        embed.title = f"🎭 {role.name}"
        embed.description = f"Créé le {discord.utils.format_dt(role.created_at, style='d')} à {discord.utils.format_dt(role.created_at, style='t')}"

        # Section: Identité
        embed.add_field(
            name="📌 Identité",
            value=f"**ID:** `{role.id}`\n**Mention:** {role.mention}",
            inline=False
        )

        # Section: Apparence
        color_hex = f"#{role.color.value:06x}" if role.color.value != 0 else "Défaut"
        embed.add_field(
            name="🎨 Apparence",
            value=f"**Couleur:** {color_hex}\n**Mentionnable:** {'✅ Oui' if role.mentionable else '❌ Non'}\n**Affichage séparé:** {'✅ Oui' if role.hoist else '❌ Non'}",
            inline=False
        )

        # Section: Membres avec pseudos - Affichage amélioré
        members_list = [m.name for m in interaction.guild.members if role in m.roles]

        if members_list:
            # Afficher 5 par ligne pour mieux lisibilité
            display_lines = []
            for i in range(0, min(len(members_list), 15), 5):
                display_lines.append(" • " + " • ".join(members_list[i:i+5]))

            members_display = "\n".join(display_lines)
            if len(members_list) > 15:
                members_display += f"\n... et {len(members_list) - 15} autres"
        else:
            members_display = "Aucun"

        embed.add_field(
            name=f"👥 Membres ({members_with_role}/{total_members})",
            value=members_display,
            inline=False
        )

        # Section: Permissions - Afficher les permissions activées en français
        perms = role.permissions
        perm_translations = {
            "administrator": "Administrateur",
            "manage_guild": "Gérer le serveur",
            "manage_roles": "Gérer les rôles",
            "manage_channels": "Gérer les salons",
            "manage_webhooks": "Gérer les webhooks",
            "create_instant_invite": "Créer des invitations",
            "kick_members": "Expulser",
            "ban_members": "Bannir",
            "manage_messages": "Gérer les messages",
            "mention_everyone": "Mentionner @everyone",
            "manage_nicknames": "Gérer les nicknames",
            "moderate_members": "Modérer les membres",
            "mute_members": "Rendre muet",
            "deafen_members": "Rendre sourd",
            "move_members": "Déplacer les membres",
            "send_messages": "Envoyer des messages",
            "send_tts_messages": "Envoyer des messages TTS",
            "add_reactions": "Utiliser les réactions",
            "read_message_history": "Lire l'historique",
            "use_external_emojis": "Utiliser les emojis externes",
            "use_external_stickers": "Utiliser les stickers externes",
            "use_application_commands": "Utiliser les slash commands",
            "connect": "Se connecter au vocal",
            "speak": "Parler",
            "use_voice_activation": "Utiliser l'activation vocale",
            "priority_speaker": "Priorité de parole",
            "view_channel": "Voir les salons",
            "manage_guild_expressions": "Gérer les emojis",
        }

        perm_list = []
        for perm_name, perm_value in perms:
            if perm_value:
                french_name = perm_translations.get(perm_name, perm_name.replace("_", " ").title())
                perm_list.append(french_name)

        if perm_list:
            embed.add_field(
                name="⚡ Permissions",
                value=" • ".join(perm_list),
                inline=False
            )

        await interaction.response.defer(); await interaction.followup.send(embed=embed)

    @info_group.command(name="user", description="Afficher les infos d'un utilisateur")
    @app_commands.describe(user="L'utilisateur à afficher")
    async def info_user(self, interaction: Interaction, user: Optional[discord.User] = None) -> None:
        """Affiche les informations d'un utilisateur."""
        if not interaction.guild:
            return

        target_user: discord.User | discord.Member = user or interaction.user
        member = await interaction.guild.fetch_member(target_user.id) if not isinstance(target_user, discord.Member) else target_user
        guild_id = interaction.guild.id

        embed = discord.Embed(
            title=f"👤 {target_user.name}",
            color=0xffa500,
            timestamp=discord.utils.utcnow()
        )
        embed.add_field(name="📌 ID", value=str(target_user.id), inline=True)
        embed.add_field(name="🤖 Type", value="Bot 🤖" if target_user.bot else "Utilisateur 👤", inline=True)
        embed.add_field(name="📅 Compte créé", value=discord.utils.format_dt(target_user.created_at, style='d'), inline=True)
        embed.add_field(name="📝 Tag", value=target_user.mention, inline=True)

        if member and member.joined_at:
            embed.add_field(name="📆 Rejoint le serveur", value=discord.utils.format_dt(member.joined_at, style='d'), inline=True)

            # Ancienneté
            days_on_server = (discord.utils.utcnow() - member.joined_at).days
            embed.add_field(name="⏳ Ancienneté", value=f"{days_on_server} jours", inline=True)

            # Rôles
            roles = [r.mention for r in member.roles if r != interaction.guild.default_role]
            if roles:
                roles_str = " ".join(roles[:8])
                if len(roles) > 8:
                    roles_str += f" +{len(roles) - 8}"
                embed.add_field(name="🎭 Rôles", value=roles_str, inline=False)

            # Surnom
            if member.nick:
                embed.add_field(name="✏️ Surnom", value=member.nick, inline=True)

            # Statut boost
            if member.premium_since:
                embed.add_field(name="💎 Booster", value=f"Depuis {discord.utils.format_dt(member.premium_since, style='d')}", inline=True)

        if target_user.avatar:
            embed.set_thumbnail(url=target_user.avatar.url)
        await interaction.response.defer(); await interaction.followup.send(embed=embed)

    # ========================================================================
    # Helper Methods - Manual Pages
    # ========================================================================
    async def _create_manual_pages(self, guild_id: int) -> List[discord.Embed]:
        """Crée les pages du manuel d'utilisation."""
        pages = []

        # Page 1: Accueil
        p1 = discord.Embed(
            title=get_text(guild_id, "general.manual.page1_title", default="Page 1/5: Accueil"),
            color=EMBED_COLORS["manual"]["p1"],
            description=get_text(guild_id, "general.manual.page1_welcome", default="Bienvenue sur Miaouscle Bot!"),
        )
        p1.add_field(
            name=get_text(guild_id, "general.manual.page1_what_does", default="🎯 À propos de Miaouscle Bot"),
            value=get_text(guild_id, "general.manual.page5_intro", default="🐱 **Miaouscle Bot** est un bot Discord complet et multilingue, conçu pour booster votre communauté avec des fonctionnalités avancées et intuitives."),
            inline=False,
        )
        p1.add_field(
            name=get_text(guild_id, "general.manual.page1_features", default="✨ Découvrez nos fonctionnalités"),
            value=get_text(guild_id, "general.manual.page5_features", default="🎮 **Système XP** - Gagnez 15-20 XP par message\n🛡️ **Modération** - 7 actions (warn, mute, ban, kick, etc)\n⚙️ **Configuration** - Personnalisable par serveur\n🌍 **Multilingue** - FR/EN/ES/DE"),
            inline=False,
        )
        p1.set_footer(text=get_text(guild_id, "general.manual.page1_footer", default="Page 1/5"))
        if self.bot.user and self.bot.user.avatar:
            p1.set_thumbnail(url=self.bot.user.avatar.url)
        pages.append(p1)

        # Page 2: XP & Niveaux
        p2 = discord.Embed(
            title=get_text(guild_id, "general.manual.page2_title", default="Page 2/5: XP & Levels"),
            color=EMBED_COLORS["manual"]["p2"],
            description=get_text(guild_id, "help.xp_system_desc", default="XP & Progression System"),
        )
        p2.add_field(
            name=get_text(guild_id, "general.manual.page2_how_xp_works", default="🎮 How Does the XP System Work?"),
            value=get_text(guild_id, "general.manual.page2_xp_description", default="XP description"),
            inline=False,
        )
        p2.set_footer(text=get_text(guild_id, "general.manual.page2_footer", default="Page 2/5"))
        pages.append(p2)

        # Page 3: Modération
        p3 = discord.Embed(
            title=get_text(guild_id, "general.manual.page3_title", default="Page 3/5: Moderation"),
            color=EMBED_COLORS["manual"]["p3"],
            description=get_text(guild_id, "help.moderation_desc", default="Moderation Tools"),
        )
        p3.add_field(
            name=get_text(guild_id, "general.manual.page3_warn", default="⚠️ Warn"),
            value=get_text(guild_id, "general.manual.page3_warn_desc", default="/warn @user reason - Avertir un utilisateur"),
            inline=False,
        )
        p3.add_field(
            name=get_text(guild_id, "general.manual.page3_mute", default="🔇 Mute"),
            value=get_text(guild_id, "general.manual.page3_mute_desc", default="/mute @user duration - Rendre muet un utilisateur"),
            inline=False,
        )
        p3.add_field(
            name=get_text(guild_id, "general.manual.page3_ban", default="🔨 Ban"),
            value=get_text(guild_id, "general.manual.page3_ban_desc", default="/ban @user reason - Bannir un utilisateur"),
            inline=False,
        )
        p3.add_field(
            name=get_text(guild_id, "general.manual.page3_kick", default="👢 Kick"),
            value=get_text(guild_id, "general.manual.page3_kick_desc", default="/kick @user reason - Expulser un utilisateur"),
            inline=False,
        )
        p3.add_field(
            name=get_text(guild_id, "general.manual.page3_sanction_list", default="📋 Sanction List"),
            value=get_text(guild_id, "general.manual.page3_sanction_list_desc", default="/sanction-liste - Voir toutes les sanctions"),
            inline=False,
        )
        p3.set_footer(text=get_text(guild_id, "general.manual.page3_footer", default="Page 3/5"))
        pages.append(p3)

        # Page 4: Configuration
        p4 = discord.Embed(
            title=get_text(guild_id, "general.manual.page4_title", default="Page 4/5: Configuration"),
            color=EMBED_COLORS["manual"]["p4"],
            description=get_text(guild_id, "help.config_desc", default="Configuration tools"),
        )
        p4.add_field(
            name="/config",
            value=get_text(guild_id, "general.manual.page4_config_desc", default="Configurez les salons de bienvenue, au revoir, logs, pseudos et les messages personnalisés"),
            inline=False,
        )
        p4.add_field(
            name=get_text(guild_id, "general.manual.page4_role_cmd", default="/donner-role @user @role - Assigner un rôle"),
            value=get_text(guild_id, "general.manual.page4_role_desc", default="Donnez ou retirez des rôles aux utilisateurs"),
            inline=False,
        )
        p4.add_field(
            name=get_text(guild_id, "general.manual.page4_language_cmd", default="🌍 Changer de langue - FR/EN/ES/DE"),
            value=get_text(guild_id, "general.manual.page4_language_desc", default="Le bot s'adapte à la langue configurée de votre serveur"),
            inline=False,
        )
        p4.set_footer(text=get_text(guild_id, "general.manual.page4_footer", default="Page 4/5"))
        pages.append(p4)

        # Page 5: Support
        p5 = discord.Embed(
            title=get_text(guild_id, "general.manual.page5_title", default="Page 5/5: Support"),
            color=EMBED_COLORS["manual"]["p5"],
            description=get_text(guild_id, "help.resources_desc", default="Support et Assistance"),
        )
        support_text = get_text(guild_id, "general.manual.page5_support_desc", default="Des questions ? Rejoignez notre serveur Discord pour obtenir de l'aide instantanée")
        support_value = f"{support_text}\n\n🔗 [Lien direct du support]({SUPPORT_URL})"
        p5.add_field(
            name=get_text(guild_id, "general.manual.page5_support_cmd", default="/support - Rejoindre le serveur de support"),
            value=support_value,
            inline=False,
        )
        p5.add_field(
            name=get_text(guild_id, "general.manual.page5_help_cmd", default="/help - Voir toutes les commandes"),
            value=get_text(guild_id, "general.manual.page5_help_desc", default="Navigation complète par catégories de commandes"),
            inline=False,
        )
        p5.add_field(
            name="🎫 Tickets & Assistance",
            value=get_text(guild_id, "general.support.tickets_desc", default="Ouvrez un ticket sur notre serveur de support pour une assistance personnalisée"),
            inline=False,
        )
        p5.set_footer(text=get_text(guild_id, "general.manual.page5_footer", default="Page 5/5 • Merci d'utiliser Miaouscle Bot! 💪😺"))
        pages.append(p5)

        return pages

    @app_commands.command(
        name="giveaway",
        description="Permet d'organiser un concours",
    )
    async def recent_modifications(self, interaction: Interaction) -> None:
        """Affiche les 3 dernières versions du CHANGELOG.md sous forme propre."""
        if interaction.user.id != OWNER_ID:
            await interaction.response.send_message(
                "❌ La commande rencontre actuellement un problème :/", ephemeral=False
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            import os
            import re
            from datetime import datetime as _dt

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            changelog_path = os.path.join(project_root, "CHANGELOG.md")

            if not os.path.exists(changelog_path):
                await interaction.followup.send(
                    "❌ CHANGELOG.md introuvable.", ephemeral=True
                )
                return

            with open(changelog_path, "r", encoding="utf-8") as f:
                content = f.read()

            # Parsing : chaque "## Version X.Y.Z - DD MMM YYYY" délimite un bloc
            VERSION_RE = re.compile(r"^## Version\s+(\S+)\s*-\s*(.+)$", re.MULTILINE)
            BOLD_RE = re.compile(r"\*\*(.+?)\*\*")
            CODE_RE = re.compile(r"`([^`]+)`")
            TAG_RE = re.compile(r"^\[(\w+)\]\s*")

            CATEGORY_EMOJI = {
                "BOT": "🤖", "SITE": "🌐", "WEB": "🌐",
                "BACKEND": "🛠️", "FIX": "🐛", "FEATURE": "✨",
            }

            def clean_md(s: str) -> str:
                s = BOLD_RE.sub(r"**\1**", s)  # garde le bold Discord (**)
                s = CODE_RE.sub(r"`\1`", s)    # garde l'inline code Discord
                return s.strip()

            matches = list(VERSION_RE.finditer(content))
            if not matches:
                await interaction.followup.send(
                    "❌ Aucune entrée de version trouvée dans le CHANGELOG.",
                    ephemeral=True,
                )
                return

            current_version = matches[0].group(1).strip()
            if not current_version.startswith("v"):
                current_version = "v" + current_version

            embed = discord.Embed(
                title="📋 Dernières mises à jour du bot",
                description=(
                    f"Version actuelle : **{current_version}**\n"
                    f"Voici les modifications les plus récentes."
                ),
                color=0x5865F2,
                timestamp=discord.utils.utcnow(),
            )

            MAX_VERSIONS = 3
            MAX_ITEMS_PER_VERSION = 4
            FIELD_VALUE_LIMIT = 1000  # Discord limite à 1024

            for i, m in enumerate(matches[:MAX_VERSIONS]):
                version = m.group(1).strip()
                if not version.startswith("v"):
                    version = "v" + version
                date = m.group(2).strip()

                start = m.end()
                end = matches[i + 1].start() if i + 1 < len(matches) else len(content)
                block = content[start:end]

                # Récupère le titre H3 (### …) si présent
                h3_match = re.search(r"^###\s+(.+)$", block, re.MULTILINE)
                heading = h3_match.group(1).strip() if h3_match else ""

                # Tous les bullet items
                items = re.findall(r"^-\s+(.+?)$", block, re.MULTILINE)
                bullets_text_lines = []
                for raw in items[:MAX_ITEMS_PER_VERSION]:
                    raw = raw.strip()
                    cat = ""
                    tm = TAG_RE.match(raw)
                    if tm:
                        tag = tm.group(1).upper()
                        cat = CATEGORY_EMOJI.get(tag, "")
                        raw = raw[tm.end():].strip()
                    # Premier segment en gras = titre court
                    bm = re.match(r"\*\*(.+?)\*\*", raw)
                    if bm:
                        short = bm.group(1).rstrip(". ").strip()
                    else:
                        short = raw.split(".")[0].strip()
                    if len(short) > 110:
                        short = short[:107].rstrip() + "…"
                    short = clean_md(short)
                    prefix = f"{cat} " if cat else "• "
                    bullets_text_lines.append(f"{prefix}{short}")

                if len(items) > MAX_ITEMS_PER_VERSION:
                    bullets_text_lines.append(
                        f"_…et {len(items) - MAX_ITEMS_PER_VERSION} autre(s) modification(s)._"
                    )

                bullets_text = "\n".join(bullets_text_lines) or "_(aucun détail)_"
                if len(bullets_text) > FIELD_VALUE_LIMIT:
                    bullets_text = bullets_text[:FIELD_VALUE_LIMIT - 1].rstrip() + "…"

                field_name = f"🏷️ {version} — {date}"
                if heading:
                    field_name = f"🏷️ {version} — {date}\n{heading[:100]}"
                    if len(field_name) > 256:
                        field_name = field_name[:253] + "…"

                embed.add_field(name=field_name, value=bullets_text, inline=False)

            embed.add_field(
                name="📊 État actuel",
                value=(
                    f"⚡ Latence : `{round(self.bot.latency * 1000)} ms`\n"
                    f"🌐 Serveurs : `{len(self.bot.guilds)}`"
                ),
                inline=False,
            )

            embed.set_footer(
                text=f"Source : CHANGELOG.md • Version {current_version}"
            )
            await interaction.followup.send(embed=embed, ephemeral=True)

        except Exception as e:
            embed = discord.Embed(
                title="❌ Erreur",
                description=f"Impossible d'afficher les modifications: `{e}`",
                color=0xFF0000,
            )
            await interaction.followup.send(embed=embed, ephemeral=True)


# ============================================================================
# Setup
# ============================================================================
async def setup(bot: commands.Bot) -> None:
    """Charge le cog de commandes générales."""
    await bot.add_cog(General(bot))
