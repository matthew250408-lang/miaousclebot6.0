import discord
from discord.ext import commands
from discord import app_commands
from utils.embeds import create_embed
from utils.database import load_config, save_config
from utils.translations import get_text, get_guild_language, set_guild_language, SUPPORTED_LANGUAGES
import json
import os
import time as _monotonic

class InactivityView(discord.ui.View):
    """Vue de base: le timer se réinitialise à chaque interaction (inactivité réelle)"""

    def _reset_inactivity_timer(self):
        """Remet le compteur à zéro — appeler au début de chaque interaction acceptée"""
        if self.timeout is not None:
            try:
                self._View__timeout_expiry = _monotonic.monotonic() + self.timeout
            except AttributeError:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        self._reset_inactivity_timer()
        return True

class ConfigView(InactivityView):
    def __init__(self, admin_user, guild_id=0):
        super().__init__(timeout=60)
        self.admin_user = admin_user
        self.message: discord.Message | None = None
        self.guild_id = guild_id
        self.setup_select_menu()

    def setup_select_menu(self):
        """Menu déroulant — un seul emoji par option, libellés épurés."""
        options = [
            discord.SelectOption(
                label="Salon de bienvenue",
                value="welcome_channel",
                description="Définir le salon des arrivées",
            ),
            discord.SelectOption(
                label="Salon des départs",
                value="goodbye_channel",
                description="Définir le salon des départs",
            ),
            discord.SelectOption(
                label="Salon de logs",
                value="logs",
                description="Centraliser les logs de modération",
            ),
            discord.SelectOption(
                label="Salon des demandes de pseudo",
                value="nickname_channel",
                description="Salon des demandes de changement de pseudo",
            ),
            discord.SelectOption(
                label="Message de bienvenue",
                value="welcome_message",
                description="Modifier le contenu textuel d'accueil",
            ),
            discord.SelectOption(
                label="Message d'au revoir",
                value="goodbye_message",
                description="Modifier le contenu textuel de départ",
            ),
            discord.SelectOption(
                label="Personnalisation Premium",
                value="premium_customize",
                description="Titre, couleur, image, footer, ping, MP",
            ),
            discord.SelectOption(
                label="Rôle Booster XP",
                value="booster_role",
                description="Rôle qui bénéficie d'un bonus d'XP",
            ),
            discord.SelectOption(
                label="Langue du bot",
                value="language",
                description="Choisir la langue du bot sur ce serveur",
            ),
            discord.SelectOption(
                label="Automodération",
                value="automod",
                description="Filtres automatiques anti-spam",
            ),
            discord.SelectOption(
                label="Réinitialiser la configuration",
                value="reset",
                description="Efface toute la configuration de ce serveur",
            ),
        ]

        select = discord.ui.Select(
            placeholder="Choisis une catégorie à configurer…",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self.config_select
        self.add_item(select)

    async def on_timeout(self) -> None:
        """Appelé quand le timeout expire"""
        if self.message:
            try:
                from utils.translations import get_text
                # On utilise guild_id depuis le message si possible, sinon FR par défaut
                guild_id = self.message.guild.id if self.message.guild else 0
                title = get_text(guild_id, "admin.config.timeout_title", default="⏱️ Session Expirée")
                desc = get_text(guild_id, "admin.config.timeout_desc", default="Le menu de configuration a expiré après 60 secondes d'inactivité.\nVeuillez relancer `/config` pour continuer à modifier.")
                embed = discord.Embed(
                    title=title,
                    description=desc,
                    color=0xff6600
                )
                embed.timestamp = discord.utils.utcnow()
                await self.message.edit(embed=embed, view=None)
            except Exception:
                pass

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False

        if interaction.user.id != self.admin_user.id:
            embed = discord.Embed(
                title=get_text(0, "admin.config.access_denied", default="❌ Accès Refusé"),
                description="Seul l'utilisateur ayant lancé la commande peut interagir.",
                color=0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        self._reset_inactivity_timer()
        return True

    def get_config_embed(self, guild_id):
        """Embed principal du panneau /config — design moderne avec sections + badge Premium."""
        from utils.premium import (
            get_guild_tier, is_free_premium_guild,
            TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS,
        )

        config = load_config(guild_id) or {}
        # Récupère le guild depuis le message OU depuis l'admin_user (1ère invocation)
        guild = None
        if self.message and self.message.guild:
            guild = self.message.guild
        elif hasattr(self.admin_user, 'guild') and self.admin_user.guild:
            guild = self.admin_user.guild

        # ─── Statut Premium (couleur + bandeau discret) ──────────────────
        # Le Premium fonctionne uniquement au niveau serveur — on affiche le
        # tier réel du serveur, sans aucun bypass utilisateur.
        tier = get_guild_tier(guild_id) if guild_id else TIER_FREE
        is_free_premium = is_free_premium_guild(guild_id) if guild_id else False
        if tier == TIER_PREMIUM_PLUS:
            color = 0xfbbf24
            tier_badge = "Premium+" + (" · Cadeau" if is_free_premium else "")
        elif tier == TIER_PREMIUM:
            color = 0x9b59b6
            tier_badge = "Premium"
        else:
            color = 0x5865F2
            tier_badge = "Gratuit · [Passer Premium](https://a7a54eed-3c9d-44cc-9bb5-f542e450c08d-00-7wjyd8rom8cz.janeway.replit.dev/premium)"

        # ─── Compteur d'options configurées ──────────────────────────────
        tracked_keys = [
            "welcome_channel", "goodbye_channel", "log_channel",
            "nickname_request_channel", "welcome_message", "goodbye_message",
            "booster_role", "language", "automod_enabled",
        ]
        configured = sum(1 for k in tracked_keys if config.get(k))
        total = len(tracked_keys)
        bar_filled = int((configured / total) * 12)
        progress_bar = "█" * bar_filled + "░" * (12 - bar_filled)

        # ─── Header épuré ────────────────────────────────────────────────
        guild_name = guild.name if guild else "ce serveur"
        embed = discord.Embed(
            title="Panneau de configuration",
            description=(
                f"### {guild_name}\n"
                f"Plan · **{tier_badge}**\n"
                f"`{progress_bar}` **{configured}/{total}** options configurées\n"
                f"\u200b"
            ),
            color=color,
        )
        if guild and guild.icon:
            embed.set_thumbnail(url=guild.icon.url)

        # Helpers
        def pill(value, fallback="—"):
            return value if value else f"`{fallback}`"

        # ─── Salons ──────────────────────────────────────────────────────
        welcome_ch = config.get("welcome_channel")
        goodbye_ch = config.get("goodbye_channel")
        log_ch = config.get("log_channel")
        nick_ch = config.get("nickname_request_channel")

        salons_value = (
            f"**Bienvenue** · {pill(f'<#{welcome_ch}>' if welcome_ch else None)}\n"
            f"**Départs** · {pill(f'<#{goodbye_ch}>' if goodbye_ch else None)}\n"
            f"**Logs** · {pill(f'<#{log_ch}>' if log_ch else None)}\n"
            f"**Demandes pseudo** · {pill(f'<#{nick_ch}>' if nick_ch else None)}"
        )
        embed.add_field(name="Salons", value=salons_value, inline=False)

        # ─── Messages ────────────────────────────────────────────────────
        def short(txt, maxlen=60):
            if not txt:
                return "`par défaut`"
            txt = str(txt)
            return f"`{txt[:maxlen] + '…' if len(txt) > maxlen else txt}`"

        msg_value = (
            f"**Bienvenue** · {short(config.get('welcome_message'))}\n"
            f"**Au revoir** · {short(config.get('goodbye_message'))}"
        )
        embed.add_field(name="Messages", value=msg_value, inline=False)

        # ─── Personnalisation Premium ────────────────────────────────────
        if tier == TIER_FREE:
            premium_value = (
                "Verrouillé — passe **Premium** pour personnaliser titre, "
                "couleur, image, footer, ping et MP de bienvenue.\n"
                "[Débloquer Premium](https://a7a54eed-3c9d-44cc-9bb5-f542e450c08d-00-7wjyd8rom8cz.janeway.replit.dev/premium)"
            )
        else:
            def state(prefix):
                bits = []
                if config.get(f"{prefix}_title"): bits.append("titre")
                if c := config.get(f"{prefix}_color"): bits.append(f"`{c}`")
                if config.get(f"{prefix}_image"): bits.append("image")
                if config.get(f"{prefix}_footer"): bits.append("footer")
                ping_default = True if prefix == "welcome" else False
                ping_v = config.get(f"{prefix}_ping", ping_default)
                bits.append("ping on" if ping_v else "ping off")
                return " · ".join(bits) if bits else "`par défaut`"

            premium_value = (
                f"**Bienvenue** · {state('welcome')}\n"
                f"**Au revoir** · {state('goodbye')}"
            )
            if tier == TIER_PREMIUM_PLUS:
                dm_on = bool(config.get("welcome_dm_enabled", False))
                premium_value += f"\n**MP bienvenue** · {'activé' if dm_on else 'désactivé'}"
            else:
                premium_value += "\n**MP bienvenue** · *Premium+ requis*"

        embed.add_field(name="Personnalisation Premium", value=premium_value, inline=False)

        # ─── Système ─────────────────────────────────────────────────────
        booster_role = config.get("booster_role")
        if booster_role:
            try:
                booster_role = int(booster_role) if isinstance(booster_role, str) else booster_role
            except Exception:
                booster_role = None
        booster_text = f"<@&{booster_role}>" if booster_role else "`—`"

        language_code = config.get("language", "fr")
        language_name = SUPPORTED_LANGUAGES.get(language_code, "🇫🇷 Français")
        automod_on = bool(config.get("automod_enabled", True))

        sys_value = (
            f"**Langue** · {language_name}\n"
            f"**Booster XP** · {booster_text}\n"
            f"**Automod** · {'activé' if automod_on else 'désactivé'}"
        )
        embed.add_field(name="Système", value=sys_value, inline=False)

        # ─── Footer ──────────────────────────────────────────────────────
        embed.set_footer(text="Choisis une catégorie dans le menu • Session 60s")
        embed.timestamp = discord.utils.utcnow()

        return embed

    async def config_select(self, interaction: discord.Interaction):
        # Les options seront générées dynamiquement avec les traductions
        if not interaction.guild:
            return

        values = interaction.data.get('values', []) if interaction.data else []
        if not values:
            return
        choice = values[0]

        guild_id = interaction.guild.id
        
        if choice == "welcome_channel":
            view = ChannelSelectView("welcome_channel", get_text(guild_id, "admin.config.welcome_channel"), self)
            embed = discord.Embed(
                title=get_text(guild_id, "admin.config.welcome_channel_title"),
                description=get_text(guild_id, "admin.config.welcome_channel_desc"),
                color=0x0099ff
            )
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "goodbye_channel":
            view = ChannelSelectView("goodbye_channel", get_text(guild_id, "admin.config.goodbye_channel"), self)
            embed = discord.Embed(
                title=get_text(guild_id, "admin.config.goodbye_channel_title"),
                description=get_text(guild_id, "admin.config.goodbye_channel_desc"),
                color=0x0099ff
            )
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "logs":
            view = LogConfigView(self)
            embed = discord.Embed(
                title=get_text(guild_id, "admin.config.logs_title"),
                description=get_text(interaction.guild.id, "admin.config.logs_desc"),
                color=0x0099ff
            )
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "nickname_channel":
            view = ChannelSelectView("nickname_request_channel", get_text(guild_id, "admin.config.nickname_channel"), self)
            embed = discord.Embed(
                title=get_text(guild_id, "admin.config.nickname_channel_title"),
                description=get_text(guild_id, "admin.config.nickname_channel_desc"),
                color=0x0099ff
            )
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "welcome_message":
            await interaction.response.send_modal(MessageModal("welcome_message", get_text(guild_id, "admin.config.welcome_message"), self))

        elif choice == "goodbye_message":
            await interaction.response.send_modal(MessageModal("goodbye_message", get_text(guild_id, "admin.config.goodbye_message"), self))

        elif choice == "booster_role":
            await interaction.response.send_modal(RoleModal("booster_role", get_text(guild_id, "admin.config.booster_role"), self))

        elif choice == "language":
            view = LanguageConfigView(self.admin_user, self)
            embed = view.get_language_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "automod":
            view = AutomodConfigView(self)
            embed = view.get_automod_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "premium_customize":
            from utils.premium import has_premium, TIER_PREMIUM
            # Premium server-only : seul le tier du serveur compte, on n'a
            # pas besoin d'un user_id pour le check.
            if not has_premium(interaction.guild.id, 0, TIER_PREMIUM):
                embed = discord.Embed(
                    title="💎 Fonctionnalité Premium",
                    description=(
                        "La personnalisation du titre, de la couleur, de l'image, du footer "
                        "et du ping des messages de bienvenue/au revoir nécessite **Miaouscle Premium**.\n\n"
                        "👉 Rends-toi sur le dashboard pour passer Premium."
                    ),
                    color=0xfbbf24,
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return
            view = PremiumCustomizeView(self.admin_user, self, surface="welcome")
            embed = view.get_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=view)

        elif choice == "reset":
            await interaction.response.send_modal(ResetConfirmModal(self))

class LogConfigView(InactivityView):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view

    def get_log_embed(self, guild_id):
        config = load_config(guild_id)
        log_channel = config.get('log_channel')
        log_notifications = config.get('log_notifications', {})

        embed = discord.Embed(
            title=get_text(guild_id, "admin.config.logs_title"),
            color=0x0099ff
        )

        # Salon de logs
        log_text = f"<#{log_channel}>" if log_channel else "Non configuré"
        embed.add_field(name=get_text(guild_id, "admin.config.log_channel_label"), value=log_text, inline=False)

        # Notifications actives
        notifications = []
        default_notifications = {
            'ban': '🔨 Bannissements',
            'unban': '🔓 Débannissements', 
            'kick': '👢 Expulsions',
            'warn': '⚠️ Avertissements',
            'mute': '🔇 Mutes',
            'unmute': '🔊 Démutes',
            'lock': '🔒 Verrouillages',
            'unlock': '🔓 Déverrouillages',
            'clear': '🧹 Suppressions'
        }

        for key, label in default_notifications.items():
            if log_notifications.get(key, True):
                notifications.append(f"✅ {label}")
            else:
                notifications.append(f"❌ {label}")

        embed.add_field(
            name=get_text(guild_id, "admin.config.notifications_active"),
            value="\n".join(notifications) if notifications else "Aucune",
            inline=False
        )

        return embed

    @discord.ui.button(label=get_text(0, "admin.config.welcome_channel"), style=discord.ButtonStyle.primary, emoji="📺")
    async def config_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        view = ChannelSelectView("log_channel", "Salon de Logs", self)
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.config.log_channel_label"),
            description=get_text(interaction.guild.id, "admin.config.logs_desc"),
            color=0x0099ff
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label=get_text(0, "admin.config.notifications_status"), style=discord.ButtonStyle.secondary, emoji="🔔")
    async def notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        view = LogNotificationsView(self)
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.config.click_to_toggle"),
            description=get_text(interaction.guild.id, "admin.config.click_to_toggle"),
            color=0x0099ff
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.primary, emoji="🔙")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await interaction.response.edit_message(
            embed=self.parent_view.get_config_embed(interaction.guild.id),
            view=self.parent_view
        )

class LogNotificationsView(InactivityView):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.add_notification_buttons()

    def add_notification_buttons(self):
        notifications = [
            ('ban', '🔨 Bannissements'),
            ('unban', '🔓 Débannissements'),
            ('kick', '👢 Expulsions'),
            ('warn', '⚠️ Avertissements'),
            ('mute', '🔇 Mutes')
        ]

        for key, label in notifications:
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"toggle_{key}"
            )
            button.callback = self.create_toggle_callback(key)
            self.add_item(button)

    def create_toggle_callback(self, notification_type):
        async def toggle_callback(interaction: discord.Interaction):
            if not interaction.guild:
                return

            config = load_config(interaction.guild.id)
            if 'log_notifications' not in config:
                config['log_notifications'] = {}

            current_state = config['log_notifications'].get(notification_type, True)
            config['log_notifications'][notification_type] = not current_state

            save_config(interaction.guild.id, config)
            await interaction.response.edit_message(
                embed=self.get_notifications_embed(interaction.guild.id),
                view=self
            )
        return toggle_callback

    def get_notifications_embed(self, guild_id):
        config = load_config(guild_id)
        log_notifications = config.get('log_notifications', {})

        embed = discord.Embed(
            title=get_text(guild_id, "admin.config.notifications_status"),
            description=get_text(guild_id, "admin.config.click_to_toggle"),
            color=0x0099ff
        )

        notifications = [
            ('ban', '🔨 Bannissements'),
            ('unban', '🔓 Débannissements'), 
            ('kick', '👢 Expulsions'),
            ('warn', '⚠️ Avertissements'),
            ('mute', '🔇 Mutes'),
            ('unmute', '🔊 Démutes'),
            ('lock', '🔒 Verrouillages'),
            ('unlock', '🔓 Déverrouillages'),
            ('clear', '🧹 Suppressions')
        ]

        status_text = []
        for key, label in notifications:
            status = "✅ Activé" if log_notifications.get(key, True) else "❌ Désactivé"
            status_text.append(f"{label}: {status}")

        embed.add_field(
            name="📋 État des Notifications",
            value="\n".join(status_text),
            inline=False
        )

        return embed

    @discord.ui.button(label=get_text(0, "admin.automod.title"), style=discord.ButtonStyle.primary, emoji="⚙️", row=1)
    async def extended_notifications_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        view = ExtendedNotificationsView(self.parent_view)
        await interaction.response.edit_message(
            embed=view.get_notifications_embed(interaction.guild.id),
            view=view
        )

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.primary, emoji="🔙", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await interaction.response.edit_message(
            embed=self.parent_view.get_log_embed(interaction.guild.id),
            view=self.parent_view
        )

class ExtendedNotificationsView(InactivityView):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.add_notification_buttons()

    def add_notification_buttons(self):
        notifications = [
            ('unmute', '🔊 Démutes'),
            ('lock', '🔒 Verrouillages'),
            ('unlock', '🔓 Déverrouillages'),
            ('clear', '🧹 Suppressions')
        ]

        for key, label in notifications:
            button = discord.ui.Button(
                label=label,
                style=discord.ButtonStyle.secondary,
                custom_id=f"toggle_{key}"
            )
            button.callback = self.create_toggle_callback(key)
            self.add_item(button)

    def create_toggle_callback(self, notification_type):
        async def toggle_callback(interaction: discord.Interaction):
            if not interaction.guild:
                return

            config = load_config(interaction.guild.id)
            if 'log_notifications' not in config:
                config['log_notifications'] = {}

            current_state = config['log_notifications'].get(notification_type, True)
            config['log_notifications'][notification_type] = not current_state

            save_config(interaction.guild.id, config)
            await interaction.response.edit_message(
                embed=self.get_notifications_embed(interaction.guild.id),
                view=self
            )
        return toggle_callback

    def get_notifications_embed(self, guild_id):
        config = load_config(guild_id)
        log_notifications = config.get('log_notifications', {})

        embed = discord.Embed(
            title=get_text(guild_id, "admin.automod.title"),
            description="Configuration des notifications supplémentaires.",
            color=0x0099ff
        )

        notifications = [
            ('unmute', '🔊 Démutes'),
            ('lock', '🔒 Verrouillages'),
            ('unlock', '🔓 Déverrouillages'),
            ('clear', '🧹 Suppressions')
        ]

        status_text = []
        for key, label in notifications:
            status = "✅ Activé" if log_notifications.get(key, True) else "❌ Désactivé"
            status_text.append(f"{label}: {status}")

        embed.add_field(
            name="📋 État des Notifications",
            value="\n".join(status_text),
            inline=False
        )

        return embed

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.primary, emoji="🔙", row=1)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        view = LogNotificationsView(self.parent_view)
        await interaction.response.edit_message(
            embed=view.get_notifications_embed(interaction.guild.id),
            view=view
        )

class ChannelSelectView(InactivityView):
    def __init__(self, config_key, title, parent_view):
        super().__init__(timeout=60)
        self.config_key = config_key
        self.title = title
        self.parent_view = parent_view

    @discord.ui.select(
        placeholder=get_text(0, "admin.config.channel_placeholder"),
        min_values=0,
        max_values=1,
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text, discord.ChannelType.news]
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if not interaction.guild:
            return

        if len(select.values) == 0:
            config = load_config(interaction.guild.id)
            config[self.config_key] = None
            save_config(interaction.guild.id, config)
        else:
            channel = select.values[0]
            config = load_config(interaction.guild.id)
            config[self.config_key] = channel.id
            save_config(interaction.guild.id, config)
            print(f"✅ CHANNEL SAVED: {self.config_key} = {channel.id} ({channel.name}) for guild {interaction.guild.id}")
        
        # Retour au parent approprié
        if hasattr(self.parent_view, 'get_automod_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_automod_embed(interaction.guild.id),
                view=self.parent_view
            )
        elif hasattr(self.parent_view, 'get_log_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_log_embed(interaction.guild.id),
                view=self.parent_view
            )
        else:
            await interaction.response.edit_message(
                embed=self.parent_view.get_config_embed(interaction.guild.id),
                view=self.parent_view
            )

    @discord.ui.button(label=get_text(0, "admin.config.disable"), style=discord.ButtonStyle.secondary, emoji="❌")
    async def disable_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        config = load_config(interaction.guild.id)
        config[self.config_key] = None
        save_config(interaction.guild.id, config)
        
        # Retour au parent approprié
        if hasattr(self.parent_view, 'get_automod_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_automod_embed(interaction.guild.id),
                view=self.parent_view
            )
        elif hasattr(self.parent_view, 'get_log_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_log_embed(interaction.guild.id),
                view=self.parent_view
            )
        else:
            await interaction.response.edit_message(
                embed=self.parent_view.get_config_embed(interaction.guild.id),
                view=self.parent_view
            )

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.primary, emoji="🔙")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        # Vérifier si parent_view est LogConfigView ou ConfigView
        if not interaction.guild:
            return

        if hasattr(self.parent_view, 'get_automod_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_automod_embed(interaction.guild.id),
                view=self.parent_view
            )
        elif hasattr(self.parent_view, 'get_log_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_log_embed(interaction.guild.id),
                view=self.parent_view
            )
        elif hasattr(self.parent_view, 'get_config_embed'):
            await interaction.response.edit_message(
                embed=self.parent_view.get_config_embed(interaction.guild.id),
                view=self.parent_view
            )

class MessageModal(discord.ui.Modal):
    def __init__(self, config_key, title, view):
        super().__init__(title=f"Configurer {title}")
        self.config_key = config_key
        self.view = view

        # Récupérer le message actuel s'il existe
        guild_id = 0
        if hasattr(view, 'admin_user') and view.admin_user and hasattr(view.admin_user, 'guild') and view.admin_user.guild:
            guild_id = view.admin_user.guild.id
        config = load_config(guild_id)
        current_message = config.get(config_key, "")

        self.message_input = discord.ui.TextInput(
            label=get_text(0, "admin.config.modal_message_label"),
            placeholder=get_text(0, "admin.config.modal_message_placeholder"),
            required=True,
            style=discord.TextStyle.paragraph,
            max_length=1000,
            default=current_message if current_message else ""
        )
        self.add_item(self.message_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return

        message = self.message_input.value.strip()

        if not message:
            embed = create_embed(
                "Erreur ❌",
                "Le message ne peut pas être vide.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Sauvegarder le message
        config = load_config(interaction.guild.id)
        config[self.config_key] = message
        save_config(interaction.guild.id, config)
        print(f"✅ MESSAGE SAVED: {self.config_key} = {message[:50]}... for guild {interaction.guild.id}")
        
        # Rafraîchir le menu principal avec les changements
        embed = self.view.get_config_embed(interaction.guild.id)
        await interaction.response.edit_message(
            embed=embed,
            view=self.view
        )
        await interaction.followup.send(f"✅ Message configuré avec succès!", ephemeral=True)


class PremiumFieldModal(discord.ui.Modal):
    """Modal générique pour éditer un champ Premium (titre, couleur, image, footer, texte MP)."""

    def __init__(self, parent_view, config_key: str, label: str, max_length: int = 256, paragraph: bool = False, placeholder: str = ""):
        super().__init__(title=f"Modifier : {label}"[:45])
        self.parent_view = parent_view
        self.config_key = config_key

        guild_id = parent_view.parent_view.guild_id if hasattr(parent_view, 'parent_view') else 0
        try:
            current = (load_config(guild_id) or {}).get(config_key, "") or ""
        except Exception:
            current = ""

        self.field = discord.ui.TextInput(
            label=label[:45],
            placeholder=placeholder[:100] if placeholder else "Laisse vide pour utiliser la valeur par défaut",
            required=False,
            style=discord.TextStyle.paragraph if paragraph else discord.TextStyle.short,
            max_length=max_length,
            default=str(current)[:max_length] if current else "",
        )
        self.add_item(self.field)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        value = self.field.value.strip()

        # Validation couleur (#RRGGBB)
        if self.config_key.endswith("_color") and value:
            v = value if value.startswith("#") else f"#{value}"
            if len(v) != 7 or any(c not in "0123456789abcdefABCDEF" for c in v[1:]):
                await interaction.response.send_message(
                    "❌ Couleur invalide. Utilise un code hex `#RRGGBB` (ex: `#00ff00`).",
                    ephemeral=True,
                )
                return
            value = v.lower()

        # Validation URL pour image
        if self.config_key.endswith("_image") and value:
            if not (value.startswith("http://") or value.startswith("https://")):
                await interaction.response.send_message(
                    "❌ URL d'image invalide. Doit commencer par `http://` ou `https://`.",
                    ephemeral=True,
                )
                return

        config = load_config(interaction.guild.id)
        if value:
            config[self.config_key] = value
        else:
            config.pop(self.config_key, None)
        save_config(interaction.guild.id, config)
        print(f"✅ PREMIUM SAVED: {self.config_key} = {str(value)[:50]} for guild {interaction.guild.id}")

        embed = self.parent_view.get_embed(interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)


class PremiumCustomizeView(InactivityView):
    """Sous-vue Premium pour personnaliser l'embed welcome/goodbye."""

    def __init__(self, admin_user, parent_view, surface: str = "welcome"):
        super().__init__(timeout=60)
        self.admin_user = admin_user
        self.parent_view = parent_view
        self.surface = surface  # "welcome" | "goodbye"
        self._build()

    def _is_plus(self, guild_id: int) -> bool:
        try:
            from utils.premium import has_premium, TIER_PREMIUM_PLUS
            # Premium server-only : seul le tier du serveur compte.
            return has_premium(guild_id, 0, TIER_PREMIUM_PLUS)
        except Exception:
            return False

    def _build(self):
        self.clear_items()

        prefix = self.surface  # welcome|goodbye
        is_welcome = prefix == "welcome"

        # ─── Ligne 0 : Sélecteur Welcome/Goodbye ────────────────────────
        surface_select = discord.ui.Select(
            placeholder=("Tu personnalises : Bienvenue" if is_welcome else "Tu personnalises : Au Revoir"),
            options=[
                discord.SelectOption(
                    label="Message de bienvenue",
                    value="welcome",
                    description="Personnaliser l'arrivée des nouveaux membres",
                    default=is_welcome,
                ),
                discord.SelectOption(
                    label="Message d'au revoir",
                    value="goodbye",
                    description="Personnaliser le départ des membres",
                    default=not is_welcome,
                ),
            ],
            row=0,
        )

        async def _on_surface(interaction: discord.Interaction):
            values = interaction.data.get('values', []) if interaction.data else []
            if values:
                self.surface = values[0]
                self._build()
                await interaction.response.edit_message(
                    embed=self.get_embed(interaction.guild.id if interaction.guild else 0),
                    view=self,
                )

        surface_select.callback = _on_surface
        self.add_item(surface_select)

        # ─── Callbacks d'édition ────────────────────────────────────────
        async def _edit_title(i: discord.Interaction):
            await i.response.send_modal(PremiumFieldModal(self, f"{prefix}_title", "Titre de l'embed", max_length=256, placeholder="🎉 Bienvenue {user} !"))

        async def _edit_color(i: discord.Interaction):
            await i.response.send_modal(PremiumFieldModal(self, f"{prefix}_color", "Couleur (#RRGGBB)", max_length=7, placeholder="#00ff00"))

        async def _edit_image(i: discord.Interaction):
            await i.response.send_modal(PremiumFieldModal(self, f"{prefix}_image", "URL de l'image bannière", max_length=500, placeholder="https://exemple.com/img.png"))

        async def _edit_footer(i: discord.Interaction):
            await i.response.send_modal(PremiumFieldModal(self, f"{prefix}_footer", "Texte du footer", max_length=2048, placeholder="Membre #{count} • {guild}"))

        async def _toggle_ping(i: discord.Interaction):
            cfg = load_config(i.guild.id) if i.guild else {}
            key = f"{prefix}_ping"
            current = cfg.get(key, True if is_welcome else False)
            cfg[key] = not bool(current)
            if i.guild:
                save_config(i.guild.id, cfg)
            self._build()
            await i.response.edit_message(
                embed=self.get_embed(i.guild.id if i.guild else 0),
                view=self,
            )

        # ─── Ligne 1 : Titre + Couleur + Ping (l'essentiel) ─────────────
        cfg_now = load_config(self.admin_user.guild.id) if (hasattr(self.admin_user, 'guild') and self.admin_user.guild) else {}
        ping_active = cfg_now.get(f"{prefix}_ping", True if is_welcome else False)

        b_title = discord.ui.Button(label="Titre", style=discord.ButtonStyle.primary, emoji="📝", row=1)
        b_title.callback = _edit_title
        self.add_item(b_title)

        b_color = discord.ui.Button(label="Couleur", style=discord.ButtonStyle.primary, emoji="🎨", row=1)
        b_color.callback = _edit_color
        self.add_item(b_color)

        b_ping = discord.ui.Button(
            label=f"Ping : {'ON' if ping_active else 'OFF'}",
            style=discord.ButtonStyle.success if ping_active else discord.ButtonStyle.secondary,
            emoji="🔔" if ping_active else "🔕",
            row=1,
        )
        b_ping.callback = _toggle_ping
        self.add_item(b_ping)

        # ─── Ligne 2 : Image + Footer (visuels) ─────────────────────────
        b_image = discord.ui.Button(label="Image bannière", style=discord.ButtonStyle.primary, emoji="🖼️", row=2)
        b_image.callback = _edit_image
        self.add_item(b_image)

        b_footer = discord.ui.Button(label="Footer", style=discord.ButtonStyle.primary, emoji="🦶", row=2)
        b_footer.callback = _edit_footer
        self.add_item(b_footer)

        # ─── Ligne 3 : Section Premium+ (uniquement welcome) ────────────
        if is_welcome:
            guild_id = self.admin_user.guild.id if (hasattr(self.admin_user, 'guild') and self.admin_user.guild) else 0
            is_plus = self._is_plus(guild_id)
            dm_on = bool(cfg_now.get("welcome_dm_enabled", False))

            async def _toggle_dm(i: discord.Interaction):
                if not self._is_plus(i.guild.id if i.guild else 0):
                    await i.response.send_message("👑 Cette option nécessite **Premium+**.", ephemeral=True)
                    return
                cfg = load_config(i.guild.id) if i.guild else {}
                cfg["welcome_dm_enabled"] = not bool(cfg.get("welcome_dm_enabled", False))
                if i.guild:
                    save_config(i.guild.id, cfg)
                self._build()
                await i.response.edit_message(embed=self.get_embed(i.guild.id if i.guild else 0), view=self)

            async def _edit_dm(i: discord.Interaction):
                if not self._is_plus(i.guild.id if i.guild else 0):
                    await i.response.send_message("👑 Cette option nécessite **Premium+**.", ephemeral=True)
                    return
                await i.response.send_modal(PremiumFieldModal(self, "welcome_dm_message", "Texte du MP", max_length=1000, paragraph=True, placeholder="Bienvenue {user} sur {guild} ! Présente-toi en #présentations 😊"))

            b_dm_toggle = discord.ui.Button(
                label=("MP : ON" if dm_on else "MP : OFF") if is_plus else "MP de bienvenue",
                style=(discord.ButtonStyle.success if dm_on else discord.ButtonStyle.secondary) if is_plus else discord.ButtonStyle.secondary,
                emoji="👑",
                row=3,
                disabled=not is_plus,
            )
            b_dm_toggle.callback = _toggle_dm
            self.add_item(b_dm_toggle)

            b_dm_text = discord.ui.Button(
                label="Texte du MP",
                style=discord.ButtonStyle.primary if is_plus else discord.ButtonStyle.secondary,
                emoji="💌",
                row=3,
                disabled=not is_plus,
            )
            b_dm_text.callback = _edit_dm
            self.add_item(b_dm_text)

        # ─── Ligne 4 : Reset surface + Retour ───────────────────────────
        async def _reset_surface(i: discord.Interaction):
            cfg = load_config(i.guild.id) if i.guild else {}
            for key in (f"{prefix}_title", f"{prefix}_color", f"{prefix}_image", f"{prefix}_footer", f"{prefix}_ping"):
                cfg.pop(key, None)
            if is_welcome:
                cfg.pop("welcome_dm_enabled", None)
                cfg.pop("welcome_dm_message", None)
            if i.guild:
                save_config(i.guild.id, cfg)
            self._build()
            await i.response.edit_message(embed=self.get_embed(i.guild.id if i.guild else 0), view=self)

        async def _back(i: discord.Interaction):
            embed = self.parent_view.get_config_embed(i.guild.id if i.guild else 0)
            await i.response.edit_message(embed=embed, view=self.parent_view)

        b_reset = discord.ui.Button(label="Réinitialiser cette surface", style=discord.ButtonStyle.secondary, emoji="♻️", row=4)
        b_reset.callback = _reset_surface
        self.add_item(b_reset)

        b_back = discord.ui.Button(label="Retour au panneau", style=discord.ButtonStyle.danger, emoji="↩️", row=4)
        b_back.callback = _back
        self.add_item(b_back)

    def get_embed(self, guild_id: int) -> discord.Embed:
        """Embed de personnalisation Premium — design moderne et lisible."""
        from utils.premium import get_guild_tier, TIER_PREMIUM_PLUS
        cfg = load_config(guild_id) or {}
        prefix = self.surface
        is_welcome = prefix == "welcome"
        surface_label = "Bienvenue" if is_welcome else "Au revoir"

        tier = get_guild_tier(guild_id) if guild_id else "free"
        is_plus = tier == TIER_PREMIUM_PLUS
        accent_color = 0xfbbf24

        def show(val, default="`par défaut`"):
            if val in (None, "", False):
                return default
            return f"`{val}`"

        title_v = cfg.get(f"{prefix}_title")
        color_v = cfg.get(f"{prefix}_color")
        image_v = cfg.get(f"{prefix}_image")
        footer_v = cfg.get(f"{prefix}_footer")
        ping_default = True if is_welcome else False
        ping_v = cfg.get(f"{prefix}_ping", ping_default)

        count = sum(1 for v in (title_v, color_v, image_v, footer_v) if v)
        if ping_v != ping_default:
            count += 1
        bar_filled = int((count / 5) * 10)
        progress = "█" * bar_filled + "░" * (10 - bar_filled)

        embed = discord.Embed(
            title="Personnalisation Premium",
            description=(
                f"### {surface_label}\n"
                f"`{progress}` **{count}/5** champs personnalisés\n"
                f"Placeholders disponibles · `{{user}}` `{{username}}` `{{guild}}` `{{count}}`\n"
                f"\u200b"
            ),
            color=accent_color,
        )

        embed.add_field(name="Titre", value=show(title_v), inline=False)
        embed.add_field(name="Couleur", value=show(color_v), inline=True)
        embed.add_field(
            name="Ping du membre",
            value="activé" if ping_v else "désactivé",
            inline=True,
        )
        embed.add_field(name="\u200b", value="\u200b", inline=True)

        if image_v and isinstance(image_v, str) and image_v.startswith(("http://", "https://")):
            embed.add_field(
                name="Image bannière",
                value=f"[Lien direct]({image_v}) · aperçu ci-dessous",
                inline=False,
            )
            embed.set_image(url=image_v)
        else:
            embed.add_field(
                name="Image bannière",
                value="`aucune` — clique sur **Image bannière** pour en ajouter",
                inline=False,
            )

        embed.add_field(name="Footer", value=show(footer_v), inline=False)

        if is_welcome:
            dm_on = bool(cfg.get("welcome_dm_enabled", False))
            dm_msg = cfg.get("welcome_dm_message") or ""
            if isinstance(dm_msg, str) and len(dm_msg) > 80:
                dm_msg_display = f"`{dm_msg[:77]}…`"
            elif dm_msg:
                dm_msg_display = f"`{dm_msg}`"
            else:
                dm_msg_display = "`reprend le message principal`"

            if is_plus:
                dm_value = (
                    f"État · {'activé' if dm_on else 'désactivé'}\n"
                    f"Message · {dm_msg_display}"
                )
            else:
                dm_value = (
                    "**Premium+ requis** — envoie un MP automatique "
                    "aux nouveaux membres.\n"
                    "[Débloquer Premium+](https://a7a54eed-3c9d-44cc-9bb5-f542e450c08d-00-7wjyd8rom8cz.janeway.replit.dev/premium)"
                )

            embed.add_field(name="MP de bienvenue", value=dm_value, inline=False)

        embed.set_footer(text=f"Premium actif • Surface : {surface_label} • Session 60s")
        embed.timestamp = discord.utils.utcnow()
        return embed

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False
        if interaction.user.id != self.admin_user.id:
            await interaction.response.send_message("❌ Accès réservé à l'utilisateur ayant lancé `/config`.", ephemeral=True)
            return False
        self._reset_inactivity_timer()
        return True


class RoleModal(discord.ui.Modal):
    def __init__(self, config_key, title, view):
        super().__init__(title=f"Configurer {title}")
        self.config_key = config_key
        self.view = view

        self.role_input = discord.ui.TextInput(
            label=get_text(0, "admin.config.modal_role_label"),
            placeholder="123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.role_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
            
        role_input = self.role_input.value.strip().lower()

        if role_input == 'none':
            config = load_config(interaction.guild.id)
            config[self.config_key] = None
            save_config(interaction.guild.id, config)
            await interaction.response.edit_message(
                embed=self.view.get_config_embed(interaction.guild.id),
                view=self.view
            )
            return

        try:
            role_id = int(role_input)
            role = interaction.guild.get_role(role_id)

            if not role:
                embed = create_embed(
                    "Erreur ❌",
                    "Rôle introuvable. Vérifiez l'ID du rôle.",
                    0xff0000
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            config = load_config(interaction.guild.id)
            config[self.config_key] = role_id
            save_config(interaction.guild.id, config)
            print(f"✅ BOOSTER ROLE SAVED: {self.config_key} = {role_id} for guild {interaction.guild.id}")
            # Recharger pour s'assurer que ça s'affiche correctement
            embed = self.view.get_config_embed(interaction.guild.id)
            await interaction.response.edit_message(
                embed=embed,
                view=self.view
            )
            await interaction.followup.send(f"✅ Rôle {role.mention} configuré comme booster XP!", ephemeral=True)

        except ValueError:
            embed = create_embed(
                "Erreur ❌",
                "ID de rôle invalide.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class LanguageConfigView(InactivityView):
    def __init__(self, admin_user, parent_view=None):
        super().__init__(timeout=60)
        self.admin_user = admin_user
        self.parent_view = parent_view

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if not interaction.guild:
            return False

        if interaction.user.id != self.admin_user.id:
            embed = create_embed(
                "Accès Refusé ❌",
                "Seul l'administrateur qui a lancé cette commande peut utiliser ces boutons.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return False
        self._reset_inactivity_timer()
        return True

    def get_language_embed(self, guild_id):
        config = load_config(guild_id)
        current_language = config.get('language', 'fr')

        embed = discord.Embed(
            title=get_text(guild_id, "admin.config.language"),
            description="Choisissez la langue dans laquelle le bot répondra sur ce serveur.",
            color=0x0099ff
        )

        embed.add_field(
            name="Langue Actuelle",
            value=SUPPORTED_LANGUAGES.get(current_language, '🇫🇷 Français'),
            inline=False
        )

        languages_text = "\n".join([f"{flag} {name}" for flag, name in SUPPORTED_LANGUAGES.items()])
        embed.add_field(
            name="Langues Disponibles",
            value=languages_text,
            inline=False
        )

        embed.set_footer(text="Cliquez sur une langue pour l'activer")
        return embed

    @discord.ui.button(label="🇫🇷 Français", style=discord.ButtonStyle.primary)
    async def french_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await self.set_language(interaction, 'fr')

    @discord.ui.button(label="🇺🇸 English", style=discord.ButtonStyle.primary)
    async def english_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await self.set_language(interaction, 'en')

    @discord.ui.button(label="🇪🇸 Español", style=discord.ButtonStyle.primary)
    async def spanish_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await self.set_language(interaction, 'es')

    @discord.ui.button(label="🇩🇪 Deutsch", style=discord.ButtonStyle.primary)
    async def german_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        await self.set_language(interaction, 'de')

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        if self.parent_view:
            embed = self.parent_view.get_config_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        else:
            config_view = ConfigView(self.admin_user, interaction.guild.id)
            embed = config_view.get_config_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=config_view)

    async def set_language(self, interaction: discord.Interaction, language_code: str):
        if not interaction.guild:
            return

        if set_guild_language(interaction.guild.id, language_code):
            print(f"✅ LANGUAGE SAVED: language = {language_code} for guild {interaction.guild.id}")
            # TOUJOURS créer une NOUVELLE ConfigView pour recréer le menu déroulant avec les traductions actualisées
            config_view = ConfigView(self.admin_user, interaction.guild.id)
            embed = config_view.get_config_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=config_view)
            await interaction.followup.send(f"✅ Langue changée en {SUPPORTED_LANGUAGES.get(language_code, '?')}!", ephemeral=True)
        else:
            embed = create_embed(
                "Erreur ❌",
                "Une erreur est survenue lors du changement de langue.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

class ResetConfirmModal(discord.ui.Modal):
    def __init__(self, view):
        super().__init__(title=get_text(0, "admin.reset.confirm_title"))
        self.view = view

        self.confirm_input = discord.ui.TextInput(
            label=get_text(0, "admin.config.modal_confirm_label"),
            placeholder="CONFIRMER",
            required=True,
            max_length=10
        )
        self.add_item(self.confirm_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return

        if self.confirm_input.value.upper() != "CONFIRMER":
            embed = create_embed(
                "Réinitialisation Annulée ❌",
                "Confirmation incorrecte.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Reset configuration
        save_config(interaction.guild.id, {})
        embed = create_embed(
            "Configuration Réinitialisée ✅",
            "Toute la configuration a été remise à zéro.",
            0x00ff00
        )

        await interaction.response.edit_message(
            embed=self.view.get_config_embed(interaction.guild.id),
            view=self.view
        )

class NicknameRequestView(discord.ui.View):
    def __init__(self, user, new_nickname):
        super().__init__(timeout=None)
        self.user = user
        self.new_nickname = new_nickname

    @discord.ui.button(label=get_text(0, "common.accept"), style=discord.ButtonStyle.success, emoji="✅")
    async def accept_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
            
        if not interaction.user.guild_permissions.manage_nicknames:
            embed = create_embed(
                "Permissions Insuffisantes ❌",
                "Vous n'avez pas les permissions pour gérer les pseudos.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            await self.user.edit(nick=self.new_nickname, reason=f"Demande acceptée par {interaction.user}")

            # MP à l'utilisateur
            try:
                embed = discord.Embed(
                    title=get_text(interaction.guild.id, "admin.nickname.accept_title"),
                    color=0x00ff00
                )
                embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.new_nickname"), value=self.new_nickname, inline=False)
                embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.accepted_by"), value=interaction.user.display_name, inline=False)
                embed.add_field(name=get_text(interaction.guild.id, "common.server"), value=interaction.guild.name, inline=False)
                await self.user.send(embed=embed)
            except:
                pass

            # Mettre à jour le message (SANS la mention ni view)
            embed = discord.Embed(
                title=get_text(interaction.guild.id, "admin.nickname.accept_title"),
                color=0x00ff00
            )
            embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.new_nickname"), value=self.new_nickname, inline=True)
            embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.accepted_by"), value=interaction.user.display_name, inline=True)
            embed.timestamp = discord.utils.utcnow()

            # Edit le message pour supprimer le contenu (mention) et les boutons
            try:
                if interaction.message:
                    await interaction.message.edit(embed=embed, view=None, content="")
            except:
                pass
            await interaction.response.defer()

        except discord.Forbidden:
            embed = create_embed(
                "Erreur ❌",
                "Je n'ai pas les permissions pour changer ce pseudo.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @discord.ui.button(label=get_text(0, "common.reject"), style=discord.ButtonStyle.danger, emoji="❌")
    async def refuse_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        if not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
            
        if not interaction.user.guild_permissions.manage_nicknames:
            embed = create_embed(
                "Permissions Insuffisantes ❌",
                "Vous n'avez pas les permissions pour gérer les pseudos.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # MP à l'utilisateur
        try:
            embed = discord.Embed(
                title=get_text(interaction.guild.id, "admin.nickname.reject_title"),
                color=0xff0000
            )
            embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.requested_nickname"), value=self.new_nickname, inline=False)
            embed.add_field(name=get_text(interaction.guild.id, "common.server"), value=interaction.guild.name, inline=False)
            embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.remark"), value=get_text(interaction.guild.id, "admin.nickname.rejection_reason"), inline=False)

            await self.user.send(embed=embed)
        except:
            pass

        # Mettre à jour le message (SANS mention ni view)
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.nickname.reject_title"),
            color=0xff0000
        )
        embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.requested_nickname"), value=self.new_nickname, inline=True)
        embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.denied_by"), value=interaction.user.display_name, inline=True)
        embed.timestamp = discord.utils.utcnow()

        try:
            if interaction.message:
                await interaction.message.edit(embed=embed, view=None, content="")
        except:
            pass
        await interaction.response.defer()

class Admin(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="config", description="⚙️ Configurer le bot pour ce serveur")
    async def config(self, interaction: discord.Interaction):
        """Affiche le menu de configuration du serveur."""
        if not interaction.guild:
            return
        
        # Invalider le cache de configuration pour forcer le rechargement depuis le fichier
        if hasattr(self.bot, 'get_cog'):
            mod_cog = self.bot.get_cog("Moderation")
            if mod_cog and hasattr(mod_cog, "_invalidate_config_cache"):
                mod_cog._invalidate_config_cache(interaction.guild.id)
        
        # Vérification des permissions (Admin ou matix)
        if not interaction.user.guild_permissions.administrator and interaction.user.id != 1140296215469240401:
            embed = discord.Embed(
                title=get_text(interaction.guild.id, "admin.config.access_denied", default="❌ Accès Refusé"),
                description="Vous devez être administrateur pour utiliser cette commande.",
                color=0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = ConfigView(interaction.user, interaction.guild.id if interaction.guild else 0)
        embed = view.get_config_embed(interaction.guild.id)
        
        await interaction.response.send_message(embed=embed, view=view)
        view.message = await interaction.original_response()

    @app_commands.command(name="donner-role", description="Donner ou retirer un rôle à un membre")
    @app_commands.describe(
        membre="Le membre à qui donner ou retirer le rôle",
        role="Le rôle à donner ou retirer",
        action="Choisir: donner ou retirer le rôle"
    )
    @app_commands.default_permissions(manage_roles=True)
    @app_commands.choices(action=[
        app_commands.Choice(name="Donner", value="donner"),
        app_commands.Choice(name="Retirer", value="retirer")
    ])
    async def donner_role(self, interaction: discord.Interaction, membre: discord.Member, role: discord.Role, action: str):
        if not interaction.guild:
            return

        await interaction.response.defer()

        # Vérifier que le rôle est gérable par le bot
        if role >= interaction.guild.me.top_role:
            embed = create_embed(
                "Erreur ❌",
                f"Je ne peux pas gérer le rôle {role.mention} (au-dessus ou égal à mon rôle principal).",
                0xff0000
            )
            await interaction.followup.send(embed=embed, ephemeral=True)
            return

        if action.lower() == "donner":
            if role in membre.roles:
                embed = create_embed(
                    "Erreur ❌",
                    f"{membre.mention} possède déjà le rôle {role.mention}.",
                    0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            if isinstance(interaction.user, discord.Member) and role >= interaction.user.top_role and interaction.user.id != interaction.guild.owner_id:
                embed = create_embed(
                    "Erreur ❌",
                    "Vous ne pouvez pas donner un rôle supérieur ou égal au vôtre.",
                    0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            try:
                await membre.add_roles(role, reason=f"Donné par {interaction.user}")

                embed = discord.Embed(
                    title="✅ Rôle Donné",
                    description=f"Le rôle {role.mention} a été attribué à {membre.mention}.",
                    color=0x00ff00
                )
                embed.set_footer(text=f"Par {interaction.user.name} • {discord.utils.utcnow().strftime('%d/%m/%Y %H:%M')}")

                await interaction.followup.send(embed=embed)

            except discord.Forbidden:
                embed = create_embed(
                    "Erreur ❌",
                    "Je n'ai pas les permissions nécessaires pour donner ce rôle.",
                    0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

        elif action.lower() == "retirer":
            if role not in membre.roles:
                embed = create_embed(
                    "Erreur ❌",
                    f"{membre.mention} ne possède pas le rôle {role.mention}.",
                    0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)
                return

            try:
                await membre.remove_roles(role, reason=f"Retiré par {interaction.user}")

                embed = discord.Embed(
                    title="❌ Rôle Retiré",
                    description=f"Le rôle {role.mention} a été retiré de {membre.mention}.",
                    color=0x00ff00
                )
                embed.set_footer(text=f"Par {interaction.user.name} • {discord.utils.utcnow().strftime('%d/%m/%Y %H:%M')}")

                await interaction.followup.send(embed=embed)

            except discord.Forbidden:
                embed = create_embed(
                    "Erreur ❌",
                    "Je n'ai pas les permissions nécessaires pour retirer ce rôle.",
                    0xff0000
                )
                await interaction.followup.send(embed=embed, ephemeral=True)

    @app_commands.command(name="changer-pseudo", description="Demander un changement de pseudo")
    @app_commands.describe(pseudo="Le nouveau pseudo souhaité")
    async def changer_pseudo(self, interaction: discord.Interaction, pseudo: str):
        if not interaction.guild:
            return

        if len(pseudo) > 15:
            embed = create_embed(
                "Erreur ❌",
                "Le pseudo ne peut pas dépasser 15 caractères.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        config = load_config(interaction.guild.id)
        nickname_channel_id = config.get('nickname_request_channel')

        if not nickname_channel_id:
            embed = create_embed(
                "Fonctionnalité Non Configurée ❌",
                "Le salon de demandes de pseudo n'est pas configuré.\nLes administrateurs peuvent le configurer avec `/config`.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        nickname_channel = interaction.guild.get_channel(nickname_channel_id)
        if not nickname_channel or not isinstance(nickname_channel, discord.TextChannel):
            embed = create_embed(
                "Erreur ❌",
                "Le salon de demandes de pseudo configuré est introuvable.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        # Créer l'embed de demande
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.nickname.new_request"),
            color=0x0099ff
        )
        embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.current_user"), value=interaction.user.mention, inline=False)
        embed.add_field(name=get_text(interaction.guild.id, "admin.nickname.desired_nickname"), value=pseudo, inline=True)
        embed.set_thumbnail(url=interaction.user.avatar.url if interaction.user.avatar else None)
        embed.timestamp = discord.utils.utcnow()

        view = NicknameRequestView(interaction.user, pseudo)

        try:
            # Mentionner le rôle UNIQUEMENT pour le serveur spécifique (1188506879052238988)
            mention_content = ""
            if interaction.guild.id == 1188506879052238988:
                mention_content = "<@&1272283488267010201>"
            
            if isinstance(nickname_channel, discord.TextChannel):
                await nickname_channel.send(content=mention_content if mention_content else None, embed=embed, view=view)

            success_embed = create_embed(
                get_text(interaction.guild.id, "admin.nickname.request_sent_title"),
                get_text(interaction.guild.id, "admin.nickname.request_sent_desc"),
                0x00ff00
            )
            await interaction.response.send_message(embed=success_embed, ephemeral=True)

        except discord.Forbidden:
            embed = create_embed(
                "Erreur ❌",
                "Je n'ai pas les permissions pour envoyer des messages dans le salon de demandes.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

    @app_commands.command(name="reponse-candidature", description="Répondre à une candidature")
    @app_commands.describe(
        membre="Le membre concerné par la candidature",
        reponse="Réponse à la candidature"
    )
    @app_commands.choices(reponse=[
        app_commands.Choice(name="Accepté", value="accepte"),
        app_commands.Choice(name="Refusé", value="refuse")
    ])
    @app_commands.default_permissions(manage_guild=True)
    async def reponse_candidature(self, interaction: discord.Interaction, membre: discord.Member, reponse: str):
        if not interaction.guild:
            return

        if reponse == "accepte":
            embed_color = 0x00ff00
            title = get_text(interaction.guild.id, "admin.candidature.accepted_title")
            message = get_text(interaction.guild.id, "admin.candidature.accepted_message")
        else:
            embed_color = 0xff0000
            title = get_text(interaction.guild.id, "admin.candidature.rejected_title")
            message = get_text(interaction.guild.id, "admin.candidature.rejected_message")

        # MP à l'utilisateur
        try:
            if not interaction.guild:
                await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
                return
            
            embed = discord.Embed(
                title=title,
                description=message,
                color=embed_color
            )
            embed.add_field(name=get_text(interaction.guild.id, "common.server"), value=interaction.guild.name, inline=False)
            embed.add_field(name=get_text(interaction.guild.id, "admin.candidature.answered_by"), value=interaction.user.display_name, inline=False)

            if reponse == "refuse":
                embed.add_field(
                    name=get_text(interaction.guild.id, "admin.nickname.remark"),
                    value=get_text(interaction.guild.id, "admin.candidature.note"),
                    inline=False
                )

            await membre.send(embed=embed)

            success_embed = create_embed(
                get_text(interaction.guild.id, "admin.candidature.sent_title"),
                get_text(interaction.guild.id, "admin.candidature.sent_message", default="La réponse de candidature a été envoyée à {member} par message privé.").format(member=membre.mention),
                0x00ff00
            )
            await interaction.response.send_message(embed=success_embed)

        except discord.Forbidden:
            error_embed = create_embed(
                "Erreur ❌",
                f"Impossible d'envoyer un message privé à {membre.mention}. Leurs MPs sont peut-être fermés.",
                0xff0000
            )
            await interaction.response.send_message(embed=error_embed, ephemeral=True)

    @app_commands.command(name="manage-bot", description="Gérer les serveurs du bot (Propriétaire uniquement)")
    async def manage_bot(self, interaction: discord.Interaction):
        # Vérifier si l'utilisateur est le propriétaire du bot
        if not interaction.guild:
            return

        BOT_OWNER_ID = int(os.getenv('BOT_OWNER_ID', '0'))  # ID du propriétaire à définir dans les secrets

        if interaction.user.id != BOT_OWNER_ID:
            embed = create_embed(
                "Accès Refusé ❌",
                "Cette commande est réservée au propriétaire du bot.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        view = ServerManagementView()
        embed = await view.get_servers_embed(self.bot)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

class ServerManagementView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=300)
        self.current_page = 0
        self.servers_per_page = 5

    async def get_servers_embed(self, bot):
        guilds = list(bot.guilds)
        total_servers = len(guilds)
        total_members = sum(guild.member_count for guild in guilds)

        # Calculer la pagination
        start_idx = self.current_page * self.servers_per_page
        end_idx = start_idx + self.servers_per_page
        current_guilds = guilds[start_idx:end_idx]
        total_pages = (total_servers + self.servers_per_page - 1) // self.servers_per_page

        embed = discord.Embed(
            title=get_text(0, "admin.servers.manage_title"),
            color=0x7289da
        )

        embed.add_field(
            name="📊 Statistiques Globales",
            value=f"🏰 **{total_servers}** serveurs\n👥 **{total_members:,}** membres au total",
            inline=False
        )

        if current_guilds:
            server_list = ""
            for i, guild in enumerate(current_guilds, start=start_idx + 1):
                owner = guild.owner
                owner_name = f"**{owner.display_name}**" if owner else "Propriétaire inconnu"

                # Créer un vrai lien d'invitation pour rejoindre le serveur
                invite_link = "Aucune permission"
                try:
                    # Trouver le premier salon où on peut créer une invitation
                    for channel in guild.text_channels:
                        if channel.permissions_for(guild.me).create_instant_invite:
                            invite = await channel.create_invite(
                                max_age=3600,  # 1 heure
                                max_uses=1,    # Une seule utilisation
                                unique=True,   # Lien unique
                                reason="Lien d'invitation pour le propriétaire du bot"
                            )
                            invite_link = f"[Rejoindre le serveur]({invite.url})"
                            break
                except Exception:
                    # Si impossible de créer un lien d'invitation
                    invite_link = "Permissions insuffisantes"

                server_list += (
                    f"**{i}.** {guild.name}\n"
                    f"  👑 Propriétaire: {owner_name}\n"
                    f"  🔗 Lien: {invite_link}\n"
                    f"  🆔 ID: `{guild.id}`\n"
                    f"  👥 Membres: {guild.member_count:,}\n"
                    f"  📅 Rejoint le: <t:{int(guild.me.joined_at.timestamp())}:d>\n\n"
                )

            embed.add_field(
                name=f"🏰 Serveurs (Page {self.current_page + 1}/{max(1, total_pages)})",
                value=server_list or "Aucun serveur",
                inline=False
            )

        embed.set_footer(text=f"Version {bot.user.name} • Page {self.current_page + 1}/{max(1, total_pages)}")
        embed.timestamp = discord.utils.utcnow()

        return embed

    @discord.ui.button(label="◀️ Précédent", style=discord.ButtonStyle.secondary, disabled=True)
    async def previous_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        self.current_page -= 1

        # Mettre à jour les boutons
        self.previous_page.disabled = (self.current_page == 0)
        total_pages = (len(interaction.client.guilds) + self.servers_per_page - 1) // self.servers_per_page
        self.next_page.disabled = (self.current_page >= total_pages - 1)

        embed = await self.get_servers_embed(interaction.client)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="Suivant ▶️", style=discord.ButtonStyle.secondary)
    async def next_page(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        total_pages = (len(interaction.client.guilds) + self.servers_per_page - 1) // self.servers_per_page
        self.current_page += 1

        # Mettre à jour les boutons
        self.previous_page.disabled = (self.current_page == 0)
        self.next_page.disabled = (self.current_page >= total_pages - 1)

        embed = await self.get_servers_embed(interaction.client)
        await interaction.response.edit_message(embed=embed, view=self)

    @discord.ui.button(label="🚪 Quitter un serveur", style=discord.ButtonStyle.danger)
    async def leave_server(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return
        
        # SÉCURITÉ: Seul le propriétaire du bot peut utiliser ce bouton
        OWNER_ID = 1140296215469240401
        if interaction.user.id != OWNER_ID:
            embed = create_embed(
                "❌ Accès Refusé",
                "Seul le propriétaire du bot peut utiliser cette fonction.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.send_modal(LeaveServerModal())

class AutomodConfigView(InactivityView):
    def __init__(self, parent_view):
        super().__init__(timeout=60)
        self.parent_view = parent_view

    def get_automod_embed(self, guild_id):
        config = load_config(guild_id)
        automod_enabled = config.get('automod_enabled', True)
        automod_channel = config.get('automod_channel')
        automod_action = config.get('automod_action', 'warn')
        automod_duration = config.get('automod_duration', 0)
        
        embed = discord.Embed(
            title=get_text(guild_id, "admin.automod.config_title"),
            color=0x00aaff
        )
        
        status = "✅ Activé" if automod_enabled else "❌ Désactivé"
        embed.add_field(name="Statut Automod", value=status, inline=True)
        
        action_text = automod_action.upper()
        if automod_action in ['mute', 'timeout']:
            action_text += f" ({automod_duration}min)"
        embed.add_field(name="⚡ Action", value=action_text, inline=True)
        
        channel_text = f"<#{automod_channel}>" if automod_channel else "Non configuré"
        embed.add_field(name="📝 Salon Logs", value=channel_text, inline=True)
        
        embed.add_field(
            name="ℹ️ Détections",
            value="• 🚫 Insultes\n• 📢 Spam (majuscules, mentions)\n• ✅ Actions automatiques",
            inline=False
        )
        
        embed.add_field(
            name="💡 Conseil",
            value="Vous pouvez créer des channels/rôles whitelist pour que l'automod ne les affecte pas.",
            inline=False
        )
        
        return embed

    @discord.ui.button(label="Activer/Désactiver", style=discord.ButtonStyle.primary, emoji="🔄")
    async def toggle_automod(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        config = load_config(interaction.guild.id)
        config['automod_enabled'] = not config.get('automod_enabled', True)
        save_config(interaction.guild.id, config)
        status = "activé" if config['automod_enabled'] else "désactivé"
        print(f"✅ AUTOMOD SAVED: automod_enabled = {config['automod_enabled']} for guild {interaction.guild.id}")
        
        # Retourner à la config automod au lieu d'envoyer un message séparé
        embed = self.get_automod_embed(interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=self)
        await interaction.followup.send(f"✅ Automod {status}!", ephemeral=True)

    @discord.ui.button(label="Choisir Action", style=discord.ButtonStyle.primary, emoji="⚡")
    async def action_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        config = load_config(interaction.guild.id)
        current_action = config.get('automod_action', 'warn')
        
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.automod.select_action_title"),
            description="Sélectionnez l'action à exécuter lors d'une violation:",
            color=0x0099ff
        )
        embed.add_field(name="🟡 WARN", value="Avertissement (simple)", inline=True)
        embed.add_field(name="🔇 MUTE", value="Silencier (timeout)", inline=True)
        embed.add_field(name="👢 KICK", value="Expulser", inline=True)
        embed.add_field(name="❌ BAN", value="Bannir", inline=True)
        
        view = ActionSelectView(self, interaction.guild.id, current_action)
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Configurer Salon", style=discord.ButtonStyle.primary, emoji="📝")
    async def config_channel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            return

        view = ChannelSelectView("automod_channel", "Salon Automod", self)
        embed = discord.Embed(
            title=get_text(interaction.guild.id, "admin.automod.config_channel"),
            description="Sélectionnez le salon où envoyer les logs d'automod.",
            color=0x0099ff
        )
        await interaction.response.edit_message(embed=embed, view=view)

    @discord.ui.button(label="Durée (Mute)", style=discord.ButtonStyle.secondary, emoji="⏱️")
    async def duration_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        config = load_config(interaction.guild.id)
        await interaction.response.send_modal(AutomodDurationModal(config.get('automod_action', 'warn'), self))

    @discord.ui.button(label=get_text(0, "common.back"), style=discord.ButtonStyle.secondary, emoji="🔙")
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        embed = self.parent_view.get_config_embed(interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)

class ActionSelectView(InactivityView):
    def __init__(self, parent_view, guild_id, current_action):
        super().__init__(timeout=60)
        self.parent_view = parent_view
        self.guild_id = guild_id
        
        for action in ['warn', 'mute', 'kick', 'ban']:
            button = discord.ui.Button(
                label=action.upper(),
                style=discord.ButtonStyle.green if action == current_action else discord.ButtonStyle.gray,
                custom_id=f"automod_action_{action}"
            )
            button.callback = self.make_callback(action)
            self.add_item(button)
        
        # Ajouter bouton retour
        back_button = discord.ui.Button(
            label=get_text(0, "common.back"),
            style=discord.ButtonStyle.secondary,
            emoji="🔙",
            custom_id="automod_back"
        )
        back_button.callback = self.back_callback
        self.add_item(back_button)
    
    async def back_callback(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        embed = self.parent_view.get_automod_embed(interaction.guild.id)
        await interaction.response.edit_message(embed=embed, view=self.parent_view)
    
    def make_callback(self, action_name: str):
        async def callback(interaction: discord.Interaction):
            if not interaction.guild:
                return
            
            config = load_config(interaction.guild.id)
            config['automod_action'] = action_name
            if action_name == 'mute' and 'automod_duration' not in config:
                config['automod_duration'] = 10
            save_config(interaction.guild.id, config)
            print(f"✅ AUTOMOD ACTION SAVED: automod_action = {action_name} for guild {interaction.guild.id}")
            
            # Retourner à la config automod
            embed = self.parent_view.get_automod_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
            await interaction.followup.send(f"✅ Action changée en {action_name.upper()}!", ephemeral=True)
        return callback

class AutomodDurationModal(discord.ui.Modal):
    def __init__(self, action: str, parent_view):
        super().__init__(title=get_text(0, "admin.automod.duration_title"))
        self.action = action
        self.parent_view = parent_view
        
        self.duration_input = discord.ui.TextInput(
            label="Durée en minutes",
            placeholder="10, 30, 60, etc.",
            required=True,
            max_length=4
        )
        self.add_item(self.duration_input)
    
    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Erreur serveur", ephemeral=True)
            return
        
        try:
            duration = int(self.duration_input.value.strip())
            if duration <= 0 or duration > 10080:  # Max 7 jours
                raise ValueError("Durée invalide")
            
            config = load_config(interaction.guild.id)
            config['automod_duration'] = duration
            save_config(interaction.guild.id, config)
            
            # Retourner au panel automod avec la mise à jour
            embed = self.parent_view.get_automod_embed(interaction.guild.id)
            await interaction.response.edit_message(embed=embed, view=self.parent_view)
        except ValueError:
            embed = create_embed("❌ Erreur", "Durée invalide (1-10080 minutes)", 0xff0000)
            await interaction.response.send_message(embed=embed, ephemeral=True)

class LeaveServerModal(discord.ui.Modal):
    def __init__(self):
        super().__init__(title=get_text(0, "admin.servers.leave_title"))

        self.server_id_input = discord.ui.TextInput(
            label="ID du serveur à quitter",
            placeholder="123456789012345678",
            required=True,
            max_length=20
        )
        self.add_item(self.server_id_input)

    async def on_submit(self, interaction: discord.Interaction):
        if not interaction.guild:
            return
        
        # SÉCURITÉ: Seul le propriétaire du bot
        OWNER_ID = 1140296215469240401
        if interaction.user.id != OWNER_ID:
            embed = create_embed(
                "❌ Accès Refusé",
                "Seul le propriétaire du bot peut utiliser cette fonction.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        try:
            server_id = int(self.server_id_input.value.strip())
            guild = interaction.client.get_guild(server_id)

            if not guild:
                embed = create_embed(
                    "Serveur Introuvable ❌",
                    "Aucun serveur trouvé avec cet ID.",
                    0xff0000
                )
                await interaction.response.send_message(embed=embed, ephemeral=True)
                return

            guild_name = guild.name
            member_count = guild.member_count

            await guild.leave()

            embed = create_embed(
                "Serveur Quitté ✅",
                f"Le bot a quitté le serveur **{guild_name}** ({member_count:,} membres)\nID: `{server_id}`",
                0x00ff00
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

        except ValueError:
            embed = create_embed(
                "Erreur ❌",
                "ID de serveur invalide.",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
        except Exception as e:
            embed = create_embed(
                "Erreur ❌",
                f"Erreur lors de la sortie du serveur: {str(e)}",
                0xff0000
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)

async def setup(bot):
    await bot.add_cog(Admin(bot))
