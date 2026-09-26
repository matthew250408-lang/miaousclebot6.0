"""
Cog des fonctionnalités Premium supplémentaires.

- /embed-custom (Premium)    : Créer un embed personnalisé (titre, description, couleur, image, footer)
- /rappel       (Premium+)   : Programmer un rappel (5min, 1h, 1j, etc.)
- /sondage      (Premium+)   : Sondage multi-options jusqu'à 10 choix avec compteur de votes
"""
import asyncio
import json
import logging
import os
import re
from datetime import datetime, timedelta, timezone
from typing import Optional

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.premium import (
    premium_only,
    TIER_PREMIUM,
    TIER_PREMIUM_PLUS,
)

logger = logging.getLogger(__name__)

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
REMINDERS_FILE = os.path.join(_PROJECT_ROOT, "web", "data", "reminders.json")
os.makedirs(os.path.dirname(REMINDERS_FILE), exist_ok=True)

POLL_EMOJIS = ["1️⃣", "2️⃣", "3️⃣", "4️⃣", "5️⃣", "6️⃣", "7️⃣", "8️⃣", "9️⃣", "🔟"]


def _load_reminders() -> list:
    try:
        with open(REMINDERS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return []


def _save_reminders(reminders: list) -> None:
    with open(REMINDERS_FILE, "w", encoding="utf-8") as f:
        json.dump(reminders, f, indent=2, ensure_ascii=False)


def _parse_duration(text: str) -> Optional[timedelta]:
    """Parse une durée comme '5m', '2h', '1d', '30s', '1h30m'."""
    if not text:
        return None
    pattern = re.compile(r"(\d+)\s*([smhdjw])", re.IGNORECASE)
    matches = pattern.findall(text.lower())
    if not matches:
        return None
    total = timedelta()
    units = {
        "s": "seconds",
        "m": "minutes",
        "h": "hours",
        "d": "days",
        "j": "days",
        "w": "weeks",
    }
    for value, unit in matches:
        kwargs = {units[unit]: int(value)}
        total += timedelta(**kwargs)
    return total if total.total_seconds() > 0 else None


def _parse_color(text: Optional[str]) -> int:
    """Parse une couleur hex (#FF0000 ou FF0000) ou un nom."""
    named = {
        "rouge": 0xef4444,
        "vert": 0x34d399,
        "bleu": 0x3b82f6,
        "jaune": 0xfbbf24,
        "violet": 0x7c3aed,
        "rose": 0xec4899,
        "orange": 0xf97316,
        "noir": 0x000000,
        "blanc": 0xffffff,
        "gris": 0x6b7280,
        "premium": 0xfbbf24,
    }
    if not text:
        return 0x7c3aed
    text = text.strip().lower()
    if text in named:
        return named[text]
    text = text.lstrip("#")
    try:
        return int(text, 16)
    except ValueError:
        return 0x7c3aed


class PremiumFeaturesCog(commands.Cog):
    """Features Premium: embed-custom, rappel, sondage."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.reminder_loop.start()

    def cog_unload(self):
        self.reminder_loop.cancel()

    # -------------------- /embed-custom (Premium) --------------------
    @app_commands.command(
        name="embed-custom",
        description="🎨 Créer un embed personnalisé (couleur, image, footer)",
    )
    @app_commands.describe(
        salon="Salon où envoyer l'embed (vide = ici)",
        titre="Titre de l'embed",
        description="Texte de l'embed (utilise \\n pour les sauts de ligne)",
        couleur="Couleur (nom: rouge/vert/bleu/violet... ou hex: #FF0000)",
        image="URL d'une image à afficher",
        thumbnail="URL d'une miniature (en haut à droite)",
        footer="Texte du footer",
        mention_role="Rôle à mentionner avec l'embed",
    )
    @premium_only(TIER_PREMIUM)
    async def embed_custom(
        self,
        interaction: discord.Interaction,
        titre: str,
        description: str,
        salon: Optional[discord.TextChannel] = None,
        couleur: Optional[str] = None,
        image: Optional[str] = None,
        thumbnail: Optional[str] = None,
        footer: Optional[str] = None,
        mention_role: Optional[discord.Role] = None,
    ):
        if not interaction.guild or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return
        if not interaction.user.guild_permissions.manage_messages:
            await interaction.response.send_message("❌ Tu dois avoir la permission de gérer les messages.", ephemeral=True)
            return

        target_channel = salon or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ Salon invalide.", ephemeral=True)
            return

        embed = discord.Embed(
            title=titre[:256],
            description=description.replace("\\n", "\n")[:4000],
            color=_parse_color(couleur),
            timestamp=datetime.now(timezone.utc),
        )
        if image:
            embed.set_image(url=image)
        if thumbnail:
            embed.set_thumbnail(url=thumbnail)
        if footer:
            embed.set_footer(text=footer[:2048])
        else:
            embed.set_footer(text=f"Envoyé par {interaction.user.display_name}")

        try:
            content = mention_role.mention if mention_role else None
            allowed = discord.AllowedMentions(roles=[mention_role]) if mention_role else discord.AllowedMentions.none()
            await target_channel.send(content=content, embed=embed, allowed_mentions=allowed)
            await interaction.response.send_message(f"✅ Embed envoyé dans {target_channel.mention}.", ephemeral=True)
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Permissions insuffisantes dans {target_channel.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Erreur: {e}", ephemeral=True)

    # -------------------- /rappel (Premium+) --------------------
    @app_commands.command(
        name="rappel",
        description="⏰ Programmer un rappel (Premium+)",
    )
    @app_commands.describe(
        duree="Durée: ex 30s, 5m, 2h, 1d, 1h30m",
        message="Message du rappel",
        salon="Salon où envoyer (vide = en MP)",
    )
    @premium_only(TIER_PREMIUM_PLUS)
    async def rappel(
        self,
        interaction: discord.Interaction,
        duree: str,
        message: str,
        salon: Optional[discord.TextChannel] = None,
    ):
        delta = _parse_duration(duree)
        if not delta:
            await interaction.response.send_message(
                "❌ Format de durée invalide. Exemples: `30s`, `5m`, `2h`, `1d`, `1h30m`.",
                ephemeral=True,
            )
            return
        if delta.total_seconds() > 30 * 24 * 3600:
            await interaction.response.send_message("❌ Durée maximum: 30 jours.", ephemeral=True)
            return

        trigger_at = datetime.now(timezone.utc) + delta
        reminders = _load_reminders()
        reminder = {
            "id": int(datetime.now(timezone.utc).timestamp() * 1000),
            "user_id": interaction.user.id,
            "channel_id": salon.id if salon else None,
            "guild_id": interaction.guild.id if interaction.guild else None,
            "message": message[:1500],
            "trigger_at": trigger_at.isoformat(),
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        reminders.append(reminder)
        _save_reminders(reminders)

        ts = int(trigger_at.timestamp())
        target = salon.mention if salon else "tes messages privés"
        embed = discord.Embed(
            title="⏰ Rappel programmé",
            description=(
                f"**Quand:** <t:{ts}:F> (<t:{ts}:R>)\n"
                f"**Où:** {target}\n"
                f"**Message:** {message[:200]}"
            ),
            color=0xfbbf24,
        )
        embed.set_footer(text="Miaouscle Bot Premium+")
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @tasks.loop(seconds=15.0)
    async def reminder_loop(self):
        """Vérifie toutes les 15 secondes les rappels à déclencher."""
        try:
            reminders = _load_reminders()
            if not reminders:
                return
            now = datetime.now(timezone.utc)
            remaining = []
            for r in reminders:
                try:
                    trigger_at = datetime.fromisoformat(r["trigger_at"].replace("Z", "+00:00"))
                except (ValueError, KeyError):
                    continue
                if trigger_at > now:
                    remaining.append(r)
                    continue
                # Déclencher
                await self._fire_reminder(r)
            if len(remaining) != len(reminders):
                _save_reminders(remaining)
        except Exception as e:
            logger.error(f"Erreur reminder_loop: {e}")

    @reminder_loop.before_loop
    async def before_reminder_loop(self):
        await self.bot.wait_until_ready()

    async def _fire_reminder(self, reminder: dict):
        try:
            user = self.bot.get_user(reminder["user_id"])
            if not user:
                try:
                    user = await self.bot.fetch_user(reminder["user_id"])
                except discord.HTTPException:
                    return

            embed = discord.Embed(
                title="⏰ Rappel",
                description=reminder.get("message", "(rappel sans message)"),
                color=0xfbbf24,
                timestamp=datetime.now(timezone.utc),
            )
            embed.set_footer(text="Miaouscle Bot Premium+")

            channel_id = reminder.get("channel_id")
            sent = False
            if channel_id:
                ch = self.bot.get_channel(int(channel_id))
                if isinstance(ch, (discord.TextChannel, discord.Thread)):
                    try:
                        await ch.send(content=user.mention, embed=embed)
                        sent = True
                    except (discord.Forbidden, discord.HTTPException):
                        pass
            if not sent:
                try:
                    await user.send(embed=embed)
                except (discord.Forbidden, discord.HTTPException):
                    logger.warning(f"Impossible d'envoyer le rappel à {user.id}")
        except Exception as e:
            logger.error(f"Erreur _fire_reminder: {e}")

    # -------------------- /sondage (Premium+) --------------------
    @app_commands.command(
        name="sondage",
        description="📊 Créer un sondage avec jusqu'à 10 options (Premium+)",
    )
    @app_commands.describe(
        question="La question du sondage",
        options="Les options séparées par |   ex: Pizza|Burger|Sushi|Salade",
        salon="Salon où envoyer (vide = ici)",
        couleur="Couleur de l'embed",
    )
    @premium_only(TIER_PREMIUM_PLUS)
    async def sondage(
        self,
        interaction: discord.Interaction,
        question: str,
        options: str,
        salon: Optional[discord.TextChannel] = None,
        couleur: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return

        opts = [o.strip() for o in options.split("|") if o.strip()]
        if len(opts) < 2:
            await interaction.response.send_message("❌ Il faut au moins 2 options séparées par `|`.", ephemeral=True)
            return
        if len(opts) > 10:
            await interaction.response.send_message("❌ Maximum 10 options.", ephemeral=True)
            return

        target_channel = salon or interaction.channel
        if not isinstance(target_channel, discord.TextChannel):
            await interaction.response.send_message("❌ Salon invalide.", ephemeral=True)
            return

        description_lines = [f"{POLL_EMOJIS[i]} **{opt}**" for i, opt in enumerate(opts)]
        embed = discord.Embed(
            title=f"📊 {question[:250]}",
            description="\n\n".join(description_lines),
            color=_parse_color(couleur),
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Sondage par {interaction.user.display_name} • Miaouscle Premium+")

        try:
            msg = await target_channel.send(embed=embed)
            for i in range(len(opts)):
                await msg.add_reaction(POLL_EMOJIS[i])
            await interaction.response.send_message(
                f"✅ Sondage publié dans {target_channel.mention}.",
                ephemeral=True,
            )
        except discord.Forbidden:
            await interaction.response.send_message(f"❌ Permissions insuffisantes dans {target_channel.mention}.", ephemeral=True)
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Erreur: {e}", ephemeral=True)


    # -------------------- /evenement (Premium+) --------------------
    evenement_group = app_commands.Group(
        name="evenement",
        description="📅 Gérer les événements Discord programmés (Premium+)",
    )

    @evenement_group.command(
        name="create",
        description="Créer un événement Discord programmé (Premium+)",
    )
    @app_commands.describe(
        nom="Nom de l'événement (1-100 caractères)",
        description="Description de l'événement",
        debut="Date/heure de début au format YYYY-MM-DD HH:MM (UTC)",
        duree="Durée (ex: 1h, 90m, 2h30m). Par défaut 1h.",
        salon_vocal="Salon vocal pour l'événement (sinon : événement externe)",
        lieu="Lieu (ex: 'Twitch', 'Salle de réunion'). Requis si pas de salon vocal.",
    )
    @premium_only(TIER_PREMIUM_PLUS)
    async def evenement_create(
        self,
        interaction: discord.Interaction,
        nom: str,
        description: str,
        debut: str,
        duree: Optional[str] = None,
        salon_vocal: Optional[discord.VoiceChannel] = None,
        lieu: Optional[str] = None,
    ):
        if not interaction.guild:
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return

        # Validation date début
        try:
            start_dt = datetime.strptime(debut.strip(), "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
        except ValueError:
            await interaction.response.send_message(
                "❌ Format de date invalide. Utilise `YYYY-MM-DD HH:MM` (UTC). Ex: `2026-05-01 19:30`.",
                ephemeral=True,
            )
            return

        if start_dt <= datetime.now(timezone.utc):
            await interaction.response.send_message(
                "❌ La date de début doit être dans le futur.", ephemeral=True
            )
            return

        # Durée
        delta = _parse_duration(duree) if duree else timedelta(hours=1)
        if not delta:
            await interaction.response.send_message(
                "❌ Durée invalide. Ex: `1h`, `90m`, `2h30m`.", ephemeral=True
            )
            return
        end_dt = start_dt + delta

        if not (1 <= len(nom) <= 100):
            await interaction.response.send_message("❌ Le nom doit faire 1-100 caractères.", ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        try:
            if salon_vocal:
                event = await interaction.guild.create_scheduled_event(
                    name=nom,
                    description=description[:1000],
                    start_time=start_dt,
                    end_time=end_dt,
                    channel=salon_vocal,
                    entity_type=discord.EntityType.voice,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
            else:
                if not lieu or not lieu.strip():
                    await interaction.followup.send(
                        "❌ Pour un événement externe il faut préciser un `lieu`.", ephemeral=True
                    )
                    return
                event = await interaction.guild.create_scheduled_event(
                    name=nom,
                    description=description[:1000],
                    start_time=start_dt,
                    end_time=end_dt,
                    location=lieu.strip()[:100],
                    entity_type=discord.EntityType.external,
                    privacy_level=discord.PrivacyLevel.guild_only,
                )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Le bot n'a pas la permission `Gérer les événements`.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(f"❌ Erreur Discord : {e}", ephemeral=True)
            return

        await interaction.followup.send(
            f"✅ Événement **{event.name}** créé.\n"
            f"📅 Début : <t:{int(start_dt.timestamp())}:F>\n"
            f"⏱ Fin : <t:{int(end_dt.timestamp())}:F>\n"
            f"🔗 {event.url}",
            ephemeral=True,
        )

    @evenement_group.command(
        name="list",
        description="Lister les événements programmés du serveur (Premium+)",
    )
    @premium_only(TIER_PREMIUM_PLUS)
    async def evenement_list(self, interaction: discord.Interaction):
        if not interaction.guild:
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return
        try:
            events = list(interaction.guild.scheduled_events)
        except Exception as e:
            await interaction.response.send_message(f"❌ Erreur récupération : {e}", ephemeral=True)
            return

        if not events:
            await interaction.response.send_message("📭 Aucun événement programmé.", ephemeral=True)
            return

        events.sort(key=lambda e: e.start_time or datetime.now(timezone.utc))
        lines = []
        for ev in events[:25]:
            ts = int(ev.start_time.timestamp()) if ev.start_time else 0
            lines.append(f"• **{ev.name}** — <t:{ts}:F> — `ID: {ev.id}`")

        embed = discord.Embed(
            title=f"📅 Événements programmés ({len(events)})",
            description="\n".join(lines),
            color=0xfbbf24,
        )
        await interaction.response.send_message(embed=embed, ephemeral=True)

    @evenement_group.command(
        name="delete",
        description="Supprimer un événement programmé par son ID (Premium+)",
    )
    @app_commands.describe(event_id="ID de l'événement (visible via /evenement list)")
    @premium_only(TIER_PREMIUM_PLUS)
    async def evenement_delete(self, interaction: discord.Interaction, event_id: str):
        if not interaction.guild:
            await interaction.response.send_message("❌ Commande à utiliser dans un serveur.", ephemeral=True)
            return
        try:
            eid = int(event_id.strip())
        except (TypeError, ValueError):
            await interaction.response.send_message("❌ ID invalide.", ephemeral=True)
            return

        event = interaction.guild.get_scheduled_event(eid)
        if not event:
            try:
                event = await interaction.guild.fetch_scheduled_event(eid)
            except (discord.NotFound, discord.HTTPException):
                event = None

        if not event:
            await interaction.response.send_message("❌ Événement introuvable.", ephemeral=True)
            return

        try:
            await event.delete()
        except discord.Forbidden:
            await interaction.response.send_message(
                "❌ Le bot n'a pas la permission de supprimer cet événement.", ephemeral=True
            )
            return
        except discord.HTTPException as e:
            await interaction.response.send_message(f"❌ Erreur : {e}", ephemeral=True)
            return

        await interaction.response.send_message(f"✅ Événement **{event.name}** supprimé.", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(PremiumFeaturesCog(bot))
