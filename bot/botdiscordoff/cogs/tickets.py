"""
cogs/tickets.py — Système de tickets professionnel.

Architecture :
- Setup : `/ticket-setup channel:#... staff_role:@... [category:?]` pose un
  panneau persistant (embed + bouton "🎫 Ouvrir un ticket") dans le canal
  indiqué. La catégorie est créée automatiquement si non fournie.
- Ouverture : un membre clique le bouton → un canal privé `📩-username-NNNN`
  est créé sous la catégorie, visible uniquement par le créateur, le rôle
  staff et le bot. Préfixe émoji et couleur dépendent du tier Premium :
    Free       → 📩 gris
    Premium    → ⚡ doré, ping staff
    Premium+   → ⭐ violet, ping staff prioritaire
- Actions dans le ticket : « 👤 Prendre en charge » et « 🔒 Fermer le ticket »
  via une persistent view (custom_ids stables, pas besoin de stocker l'ID
  de ticket dans le custom_id).
- Storage : web/data/tickets.json — config par serveur + index des tickets
  ouverts pour pouvoir restaurer l'état après redémarrage.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from datetime import datetime, time as dtime, timezone
from typing import Optional

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover
    ZoneInfo = None  # type: ignore

import discord
from discord import app_commands
from discord.ext import commands, tasks

from utils.premium import (
    TIER_FREE, TIER_PREMIUM, TIER_PREMIUM_PLUS,
    get_guild_tier, get_user_customer_tier, OWNER_IDS,
)

logger = logging.getLogger(__name__)

# ============================================================================
# SLA — Service Level Agreement (temps de réponse promis par tier)
# ============================================================================
# Ces valeurs DOIVENT correspondre à ce qui est annoncé sur la page de tarifs
# (web/templates/premium.html) et dans l'embed /premium.
# Un ticket est considéré "non répondu" tant que `claimed_at` est absent.
_SLA_HOURS = {
    TIER_PREMIUM: 24,
    TIER_PREMIUM_PLUS: 6,
}
# Délai entre 2 alertes pour le même ticket dépassé (évite de spammer l'owner
# si le ticket reste sans réponse pendant des jours).
_SLA_REALERT_HOURS = 6
# DM envoyé à l'owner principal du bot (le seul qui paie l'infra).
_SLA_ALERT_OWNER_ID = 1140296215469240401

# Canal où envoyer le récapitulatif après fermeture de ticket
_RECAP_CHANNEL_ID = 1240262613867298836


# ============================================================================
# STORAGE
# ============================================================================

_STORAGE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "web", "data", "tickets.json",
)
_STORAGE_LOCK = asyncio.Lock()


def _load_storage() -> dict:
    try:
        with open(_STORAGE_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_storage(data: dict) -> None:
    os.makedirs(os.path.dirname(_STORAGE_PATH), exist_ok=True)
    tmp = _STORAGE_PATH + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)
    os.replace(tmp, _STORAGE_PATH)


def _get_guild_cfg(gid: int) -> dict:
    data = _load_storage()
    return data.get(str(gid), {}) or {}


async def _update_guild_cfg(gid: int, mutator) -> dict:
    """Charge → applique mutator(cfg) → sauve. Retourne la config finale."""
    async with _STORAGE_LOCK:
        data = _load_storage()
        cfg = data.get(str(gid), {}) or {}
        mutator(cfg)
        data[str(gid)] = cfg
        _save_storage(data)
        return cfg


# ============================================================================
# TIER VISUALS
# ============================================================================

_TIER_THEME = {
    TIER_FREE:         {"emoji": "📩", "color": 0x9ca3af, "label": "Standard",   "ping": False},
    TIER_PREMIUM:      {"emoji": "⚡", "color": 0xfbbf24, "label": "Premium",    "ping": True},
    TIER_PREMIUM_PLUS: {"emoji": "⭐", "color": 0xa855f7, "label": "Premium+",   "ping": True},
}


def _theme(tier: str) -> dict:
    return _TIER_THEME.get(tier, _TIER_THEME[TIER_FREE])


_USERNAME_SAFE_RE = re.compile(r"[^a-z0-9-]+")


def _safe_username_for_channel(name: str) -> str:
    name = name.lower()
    name = _USERNAME_SAFE_RE.sub("-", name).strip("-")
    return (name[:32] or "user")


# ============================================================================
# OPENING HOURS
# ============================================================================
# Stockage : cfg["hours"] = {
#     "enabled": bool,
#     "days":   [0..6]    (0=lundi … 6=dimanche),
#     "open":   "HH:MM",
#     "close":  "HH:MM",
#     "tz":     "Europe/Paris",
#     "message": "Texte optionnel affiché quand fermé",
# }

_DAY_NAMES_FR = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
_DAY_SHORT_FR = ["lun", "mar", "mer", "jeu", "ven", "sam", "dim"]
_TIME_RE = re.compile(r"^(\d{1,2}):(\d{2})$")


def _parse_time_str(s: str) -> Optional[dtime]:
    m = _TIME_RE.match(s.strip())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2))
    if not (0 <= h <= 23 and 0 <= mi <= 59):
        return None
    return dtime(hour=h, minute=mi)


def _parse_days_str(s: str) -> Optional[list[int]]:
    """Accepte : 'tous', 'semaine', 'weekend', ou 'lun,mar,jeu' / 'lun-ven'."""
    s = s.strip().lower()
    if s in ("tous", "tout", "7j7", "7/7", "all"):
        return [0, 1, 2, 3, 4, 5, 6]
    if s in ("semaine", "ouvre", "ouvrable", "ouvrables", "lun-ven"):
        return [0, 1, 2, 3, 4]
    if s in ("weekend", "week-end", "we"):
        return [5, 6]
    out: set[int] = set()
    parts = re.split(r"[\s,]+", s)
    for p in parts:
        if not p:
            continue
        if "-" in p:
            a, b = p.split("-", 1)
            if a in _DAY_SHORT_FR and b in _DAY_SHORT_FR:
                ia, ib = _DAY_SHORT_FR.index(a), _DAY_SHORT_FR.index(b)
                if ia <= ib:
                    out.update(range(ia, ib + 1))
                    continue
            return None
        if p in _DAY_SHORT_FR:
            out.add(_DAY_SHORT_FR.index(p))
        else:
            return None
    if not out:
        return None
    return sorted(out)


def _format_days(days: list[int]) -> str:
    if not days:
        return "_aucun_"
    if days == [0, 1, 2, 3, 4, 5, 6]:
        return "Tous les jours"
    if days == [0, 1, 2, 3, 4]:
        return "Du lundi au vendredi"
    if days == [5, 6]:
        return "Week-end (samedi & dimanche)"
    return ", ".join(_DAY_NAMES_FR[d].capitalize() for d in days)


def _is_within_hours(hours_cfg: dict) -> tuple[bool, str]:
    """Renvoie (ouvert?, raison_si_fermé)."""
    if not hours_cfg or not hours_cfg.get("enabled"):
        return True, ""
    tz_name = hours_cfg.get("tz", "Europe/Paris")
    try:
        tz = ZoneInfo(tz_name) if ZoneInfo else timezone.utc
    except Exception:
        tz = timezone.utc
    now = datetime.now(tz)
    weekday = now.weekday()  # 0=lun
    days = hours_cfg.get("days") or []
    if weekday not in days:
        return False, f"Le support est fermé aujourd'hui ({_DAY_NAMES_FR[weekday]})."
    open_t = _parse_time_str(hours_cfg.get("open", "00:00")) or dtime(0, 0)
    close_t = _parse_time_str(hours_cfg.get("close", "23:59")) or dtime(23, 59)
    cur = now.time().replace(second=0, microsecond=0)
    if open_t <= close_t:
        if not (open_t <= cur <= close_t):
            return False, (
                f"Le support est ouvert {open_t.strftime('%H:%M')}–{close_t.strftime('%H:%M')} "
                f"(fuseau {tz_name})."
            )
    else:
        # plage qui passe minuit (ex: 22:00 → 04:00)
        if not (cur >= open_t or cur <= close_t):
            return False, (
                f"Le support est ouvert {open_t.strftime('%H:%M')}–{close_t.strftime('%H:%M')} "
                f"(fuseau {tz_name})."
            )
    return True, ""


# ============================================================================
# PERSISTENT VIEWS
# ============================================================================

class OpenTicketView(discord.ui.View):
    """Vue persistante posée dans le canal d'accueil — un seul bouton."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Ouvrir un ticket",
        emoji="🎫",
        style=discord.ButtonStyle.primary,
        custom_id="miaouscle:ticket:open",
    )
    async def open_ticket(self, interaction: discord.Interaction, button: discord.ui.Button):
        await TicketsCog.handle_open_ticket(interaction)


class TicketActionsView(discord.ui.View):
    """Vue persistante posée DANS chaque ticket — Claim + Fermer."""

    def __init__(self):
        super().__init__(timeout=None)

    @discord.ui.button(
        label="Prendre en charge",
        emoji="👤",
        style=discord.ButtonStyle.secondary,
        custom_id="miaouscle:ticket:claim",
    )
    async def claim(self, interaction: discord.Interaction, button: discord.ui.Button):
        await TicketsCog.handle_claim_ticket(interaction)

    @discord.ui.button(
        label="Fermer le ticket",
        emoji="🔒",
        style=discord.ButtonStyle.danger,
        custom_id="miaouscle:ticket:close",
    )
    async def close(self, interaction: discord.Interaction, button: discord.ui.Button):
        await TicketsCog.handle_close_ticket(interaction)


class CloseConfirmView(discord.ui.View):
    """Confirmation éphémère avant suppression du canal."""

    def __init__(self, ticket_channel_id: int, requester_id: int):
        super().__init__(timeout=30)
        self.ticket_channel_id = ticket_channel_id
        self.requester_id = requester_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.requester_id:
            await interaction.response.send_message(
                "Cette confirmation n'est pas pour toi.", ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="Confirmer la fermeture", emoji="🔒",
                       style=discord.ButtonStyle.danger)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(
            content="🔒 Ticket en cours de fermeture…", view=None,
        )
        await TicketsCog.do_close_ticket(interaction.guild, self.ticket_channel_id,
                                          closed_by=interaction.user)

    @discord.ui.button(label="Annuler", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        await interaction.response.edit_message(content="❌ Fermeture annulée.", view=None)


# ============================================================================
# COG
# ============================================================================

class TicketsCog(commands.Cog):
    """Gestion des tickets de support — pro et persistant."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot

    async def cog_load(self):
        # Enregistre les persistent views au chargement du cog (idempotent côté discord.py)
        self.bot.add_view(OpenTicketView())
        self.bot.add_view(TicketActionsView())
        # Démarre la surveillance SLA (DM owner si un ticket dépasse les 24h/6h)
        if not self.sla_monitor.is_running():
            self.sla_monitor.start()

    async def cog_unload(self):
        if self.sla_monitor.is_running():
            self.sla_monitor.cancel()

    # ----------------------------------------------------- surveillance SLA
    @tasks.loop(minutes=15)
    async def sla_monitor(self):
        """Scanne tous les tickets ouverts et alerte l'owner principal en MP
        quand un ticket Premium/Premium+ dépasse son SLA sans réponse staff."""
        try:
            await self._check_sla_breaches()
        except Exception as e:
            logger.exception("sla_monitor: erreur scan SLA: %s", e)

    @sla_monitor.before_loop
    async def _sla_monitor_ready(self):
        await self.bot.wait_until_ready()

    async def _check_sla_breaches(self):
        now = datetime.now(timezone.utc)
        data = _load_storage()
        if not isinstance(data, dict):
            return

        breached = []   # liste de dicts à inclure dans le DM
        updates = []    # (gid, ticket_channel_id) à marquer alerted

        for gid_str, gcfg in data.items():
            if not isinstance(gcfg, dict):
                continue
            tickets = gcfg.get("tickets") or {}
            for ch_id, t in tickets.items():
                if not isinstance(t, dict):
                    continue
                if t.get("status") != "open":
                    continue
                # Si déjà claim/répondu par le staff → pas d'alerte
                if t.get("claimed_at"):
                    continue
                tier = t.get("tier")
                sla_hours = _SLA_HOURS.get(tier)
                if not sla_hours:
                    continue  # pas de SLA pour les tickets Free
                opened_at_str = t.get("opened_at")
                if not opened_at_str:
                    continue
                try:
                    opened_at = datetime.fromisoformat(opened_at_str)
                except ValueError:
                    continue
                if opened_at.tzinfo is None:
                    opened_at = opened_at.replace(tzinfo=timezone.utc)
                age = now - opened_at
                if age.total_seconds() < sla_hours * 3600:
                    continue  # SLA pas encore dépassé

                # Anti-spam : ne pas re-alerter avant _SLA_REALERT_HOURS
                last_alert_str = t.get("sla_alerted_at")
                if last_alert_str:
                    try:
                        last_alert = datetime.fromisoformat(last_alert_str)
                        if last_alert.tzinfo is None:
                            last_alert = last_alert.replace(tzinfo=timezone.utc)
                        if (now - last_alert).total_seconds() < _SLA_REALERT_HOURS * 3600:
                            continue
                    except ValueError:
                        pass

                try:
                    gid_int = int(gid_str)
                    ch_id_int = int(ch_id)
                except (TypeError, ValueError):
                    continue

                guild_obj = self.bot.get_guild(gid_int)
                guild_name = guild_obj.name if guild_obj else f"Serveur {gid_str}"
                breached.append({
                    "guild_id": gid_int,
                    "guild_name": guild_name,
                    "channel_id": ch_id_int,
                    "number": t.get("number") or 0,
                    "tier": tier,
                    "creator_id": t.get("creator_id"),
                    "age_hours": age.total_seconds() / 3600,
                    "sla_hours": sla_hours,
                })
                updates.append((gid_str, ch_id))

        if not breached:
            return

        # Envoie le DM à l'owner principal
        try:
            owner_user = self.bot.get_user(_SLA_ALERT_OWNER_ID) or await self.bot.fetch_user(_SLA_ALERT_OWNER_ID)
        except discord.HTTPException as e:
            logger.warning("sla_monitor: impossible de récupérer l'owner %s: %s",
                           _SLA_ALERT_OWNER_ID, e)
            return
        if owner_user is None:
            return

        # Trie par dépassement le plus grave d'abord
        breached.sort(key=lambda b: (b["age_hours"] - b["sla_hours"]), reverse=True)

        embed = discord.Embed(
            title=f"🚨 SLA dépassé — {len(breached)} ticket(s) en retard",
            description=(
                "Ces tickets clients **Premium / Premium+** n'ont pas encore été "
                "pris en charge par le staff alors que le délai promis est dépassé.\n"
                "Clique sur le lien pour ouvrir le ticket directement."
            ),
            color=0xef4444,
            timestamp=now,
        )
        # Discord limite à 25 fields → on tronque si jamais
        for b in breached[:25]:
            tier_label = _theme(b["tier"])["label"]
            over_h = b["age_hours"] - b["sla_hours"]
            creator = f"<@{b['creator_id']}>" if b.get("creator_id") else "—"
            value = (
                f"**Serveur** : {b['guild_name']}\n"
                f"**Plan** : {tier_label} (SLA {b['sla_hours']}h)\n"
                f"**Ouvert il y a** : {b['age_hours']:.1f}h "
                f"(⏰ +{over_h:.1f}h de retard)\n"
                f"**Membre** : {creator}\n"
                f"**Salon** : <#{b['channel_id']}>"
            )
            embed.add_field(
                name=f"#{b['number']:04d}",
                value=value,
                inline=False,
            )
        if len(breached) > 25:
            embed.set_footer(
                text=f"+ {len(breached) - 25} autres tickets non listés (limite Discord)"
            )

        try:
            await owner_user.send(embed=embed)
        except discord.Forbidden:
            logger.warning("sla_monitor: l'owner %s a bloqué les MP du bot",
                           _SLA_ALERT_OWNER_ID)
            return
        except discord.HTTPException as e:
            logger.warning("sla_monitor: envoi DM owner échoué: %s", e)
            return

        # Marque les tickets comme "alerted" pour ne pas re-pinger avant 6h
        async with _STORAGE_LOCK:
            data2 = _load_storage()
            for gid_str, ch_id in updates:
                gcfg = data2.get(gid_str)
                if not isinstance(gcfg, dict):
                    continue
                tickets = gcfg.get("tickets") or {}
                t = tickets.get(ch_id)
                if isinstance(t, dict):
                    t["sla_alerted_at"] = now.isoformat()
            _save_storage(data2)

    # ---------------------------------------------------------------- helpers

    @staticmethod
    def _is_admin(member: discord.Member) -> bool:
        p = member.guild_permissions
        return p.administrator or p.manage_guild

    @staticmethod
    def _can_admin_ticket(interaction: discord.Interaction) -> bool:
        """Autorise uniquement les propriétaires du bot."""
        return interaction.user.id in OWNER_IDS

    @staticmethod
    async def _ensure_category(guild: discord.Guild, cfg: dict) -> Optional[discord.CategoryChannel]:
        """Retourne la catégorie configurée, la (re)crée si nécessaire."""
        cat_id = cfg.get("category_id")
        if cat_id:
            cat = guild.get_channel(int(cat_id))
            if isinstance(cat, discord.CategoryChannel):
                return cat
        # crée une catégorie par défaut
        try:
            cat = await guild.create_category(
                name="🎫 Tickets",
                reason="Création automatique catégorie tickets",
            )
            await _update_guild_cfg(guild.id, lambda c: c.update({"category_id": str(cat.id)}))
            return cat
        except discord.Forbidden:
            return None

    # ---------------------------------------------------- commande /ticket-setup

    @app_commands.command(
        name="ticket-setup",
        description="Pose le panneau de tickets dans un canal et configure le rôle staff.",
    )
    @app_commands.describe(
        channel="Le salon dans lequel poster le panneau public (les membres y cliqueront)",
        staff_role="Le rôle qui pourra voir et répondre dans tous les tickets",
        category="Optionnel : catégorie où créer les tickets (sinon une est créée auto)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def ticket_setup(
        self,
        interaction: discord.Interaction,
        channel: discord.TextChannel,
        staff_role: discord.Role,
        category: Optional[discord.CategoryChannel] = None,
    ):
        if not self._can_admin_ticket(interaction):
            await interaction.response.send_message(
                "❌ Réservé aux administrateurs du serveur.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        guild = interaction.guild
        # Vérifie permissions du bot dans le canal du panneau
        me = guild.me
        perms = channel.permissions_for(me)
        if not (perms.view_channel and perms.send_messages and perms.embed_links):
            await interaction.followup.send(
                f"❌ Je n'ai pas les permissions nécessaires dans {channel.mention} "
                f"(View Channel, Send Messages, Embed Links).",
                ephemeral=True,
            )
            return

        # Sauvegarde config
        def _set(c: dict):
            c["panel_channel_id"] = str(channel.id)
            c["staff_role_id"] = str(staff_role.id)
            if category is not None:
                c["category_id"] = str(category.id)
            c.setdefault("next_number", 1)
            c.setdefault("tickets", {})

        cfg = await _update_guild_cfg(guild.id, _set)

        # S'assure qu'une catégorie existe
        cat = await self._ensure_category(guild, cfg)
        if cat is None:
            await interaction.followup.send(
                "⚠️ Configuration enregistrée, mais je n'ai pas pu créer la catégorie « 🎫 Tickets » "
                "(permission `Manage Channels` manquante). Crée-la à la main, ou repasse "
                "`/ticket-setup` avec l'option `category`.",
                ephemeral=True,
            )

        # Poste le panneau
        embed = discord.Embed(
            title="🎫 Support — Ouvre un ticket",
            description=(
                "Besoin d'aide, d'un signalement ou d'une question ?\n"
                "Clique sur le bouton ci-dessous pour créer un **canal privé** entre toi "
                "et l'équipe de modération.\n\n"
                "⏱️ **Temps de réponse selon ton plan :**\n"
                "📩 **Basic** — File d'attente standard *(aucun SLA)*\n"
                "⚡ **Premium** — Réponse sous **24 h**, ping staff à l'ouverture\n"
                "⭐ **Premium+** — Réponse sous **6 h**, ping staff + priorité absolue"
            ),
            color=0x5865f2,
        )
        embed.set_footer(text="Miaouscle Tickets • Un seul ticket ouvert à la fois par membre")
        try:
            await channel.send(embed=embed, view=OpenTicketView())
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Impossible de poster le panneau : {e}", ephemeral=True,
            )
            return

        await interaction.followup.send(
            f"✅ Panneau de tickets posé dans {channel.mention}.\n"
            f"• Rôle staff : {staff_role.mention}\n"
            f"• Catégorie : {cat.mention if cat else '_à créer manuellement_'}",
            ephemeral=True,
        )

    # ---------------------------------------------------- commande /ticket-config

    @app_commands.command(
        name="ticket-config",
        description="Affiche ou modifie la configuration des tickets (incl. horaires d'ouverture).",
    )
    @app_commands.describe(
        horaires_activer="Active (True) ou désactive (False) le filtrage par horaires d'ouverture",
        horaires_jours="Jours d'ouverture : tous, semaine, weekend, ou liste (ex: lun,mar,jeu ou lun-ven)",
        horaires_debut="Heure d'ouverture (format HH:MM, ex: 09:00)",
        horaires_fin="Heure de fermeture (format HH:MM, ex: 18:00)",
        horaires_fuseau="Fuseau horaire IANA (défaut Europe/Paris ; ex: Europe/Brussels, America/Montreal)",
        horaires_message="Message affiché aux membres quand le support est fermé (optionnel)",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def ticket_config(
        self,
        interaction: discord.Interaction,
        horaires_activer: Optional[bool] = None,
        horaires_jours: Optional[str] = None,
        horaires_debut: Optional[str] = None,
        horaires_fin: Optional[str] = None,
        horaires_fuseau: Optional[str] = None,
        horaires_message: Optional[str] = None,
    ):
        if not self._can_admin_ticket(interaction):
            await interaction.response.send_message(
                "❌ Réservé aux administrateurs du serveur.", ephemeral=True,
            )
            return

        cfg = _get_guild_cfg(interaction.guild.id)
        if not cfg.get("panel_channel_id"):
            await interaction.response.send_message(
                "⚠️ Aucun panneau configuré. Utilise `/ticket-setup` d'abord.",
                ephemeral=True,
            )
            return

        # ----- Modification des horaires si demandée ----------------------------
        any_hour_param = any(
            v is not None for v in (
                horaires_activer, horaires_jours, horaires_debut, horaires_fin,
                horaires_fuseau, horaires_message,
            )
        )

        if any_hour_param:
            current = cfg.get("hours") or {}

            # Désactivation explicite
            if horaires_activer is False:
                def _disable(c: dict):
                    h = c.get("hours") or {}
                    h["enabled"] = False
                    c["hours"] = h
                await _update_guild_cfg(interaction.guild.id, _disable)
                await interaction.response.send_message(
                    "✅ Horaires d'ouverture **désactivés**. Les tickets sont ouvrables 24/7.",
                    ephemeral=True,
                )
                return

            # Activation / modification : on fusionne avec la config existante
            new_days = _parse_days_str(horaires_jours) if horaires_jours else current.get("days")
            if horaires_jours and new_days is None:
                await interaction.response.send_message(
                    "❌ `horaires_jours` invalide. Utilise `tous`, `semaine`, `weekend`, "
                    "ou une liste comme `lun,mar,jeu` ou `lun-ven`.",
                    ephemeral=True,
                )
                return

            if horaires_debut:
                t = _parse_time_str(horaires_debut)
                if t is None:
                    await interaction.response.send_message(
                        "❌ `horaires_debut` invalide. Format attendu : HH:MM (ex: `09:00`).",
                        ephemeral=True,
                    )
                    return
                new_open = f"{t.hour:02d}:{t.minute:02d}"
            else:
                new_open = current.get("open")

            if horaires_fin:
                t = _parse_time_str(horaires_fin)
                if t is None:
                    await interaction.response.send_message(
                        "❌ `horaires_fin` invalide. Format attendu : HH:MM (ex: `18:00`).",
                        ephemeral=True,
                    )
                    return
                new_close = f"{t.hour:02d}:{t.minute:02d}"
            else:
                new_close = current.get("close")

            new_tz = (horaires_fuseau or current.get("tz") or "Europe/Paris").strip()
            if ZoneInfo is not None:
                try:
                    ZoneInfo(new_tz)
                except Exception:
                    await interaction.response.send_message(
                        f"❌ Fuseau horaire `{new_tz}` inconnu. Utilise un identifiant IANA "
                        "(ex: `Europe/Paris`, `America/Montreal`).",
                        ephemeral=True,
                    )
                    return

            new_message = (
                horaires_message.strip() if isinstance(horaires_message, str)
                else current.get("message")
            ) or None

            # Activation par défaut si on touche à n'importe quel paramètre horaire
            # sans préciser horaires_activer.
            enabled = True if horaires_activer is None else bool(horaires_activer)

            # On exige au moins jours + début + fin pour activer
            if enabled and (not new_days or not new_open or not new_close):
                await interaction.response.send_message(
                    "❌ Pour activer les horaires, indique au moins `horaires_jours`, "
                    "`horaires_debut` et `horaires_fin`.\nExemple : "
                    "`/ticket-config horaires_activer:True horaires_jours:lun-ven "
                    "horaires_debut:09:00 horaires_fin:18:00`",
                    ephemeral=True,
                )
                return

            def _set_hours(c: dict):
                c["hours"] = {
                    "enabled": enabled,
                    "days": new_days or [],
                    "open": new_open or "00:00",
                    "close": new_close or "23:59",
                    "tz": new_tz,
                    "message": new_message,
                }
            await _update_guild_cfg(interaction.guild.id, _set_hours)
            cfg = _get_guild_cfg(interaction.guild.id)  # recharge pour l'affichage

        # ----- Affichage de la configuration actuelle ---------------------------
        guild = interaction.guild
        panel = guild.get_channel(int(cfg["panel_channel_id"])) if cfg.get("panel_channel_id") else None
        cat = guild.get_channel(int(cfg["category_id"])) if cfg.get("category_id") else None
        role = guild.get_role(int(cfg["staff_role_id"])) if cfg.get("staff_role_id") else None
        open_count = sum(1 for t in cfg.get("tickets", {}).values() if t.get("status") == "open")
        embed = discord.Embed(
            title="🎫 Configuration tickets",
            color=0x5865f2,
            timestamp=datetime.now(timezone.utc),
        )
        if any_hour_param:
            embed.description = "✅ Horaires d'ouverture mis à jour."
        embed.add_field(name="Panneau", value=panel.mention if panel else "_introuvable_", inline=False)
        embed.add_field(name="Catégorie", value=cat.mention if cat else "_introuvable_", inline=True)
        embed.add_field(name="Rôle staff", value=role.mention if role else "_introuvable_", inline=True)
        embed.add_field(name="Tickets ouverts", value=str(open_count), inline=True)
        embed.add_field(name="Prochain numéro", value=f"#{cfg.get('next_number', 1):04d}", inline=True)
        # Horaires d'ouverture
        h = cfg.get("hours") or {}
        if h.get("enabled"):
            hours_value = (
                f"🟢 **Activés**\n"
                f"• Jours : {_format_days(h.get('days') or [])}\n"
                f"• Plage : {h.get('open','--:--')}–{h.get('close','--:--')} ({h.get('tz','Europe/Paris')})"
            )
            if h.get("message"):
                hours_value += f"\n• Message : *{h['message']}*"
        else:
            hours_value = (
                "⚪ Désactivés (tickets ouvrables 24/7)\n"
                "_Pour activer : ajoute `horaires_activer:True horaires_jours:… "
                "horaires_debut:HH:MM horaires_fin:HH:MM` à cette commande._"
            )
        embed.add_field(name="🕘 Horaires d'ouverture", value=hours_value, inline=False)
        await interaction.response.send_message(embed=embed, ephemeral=True)

    # ---------------------------------------------------- commande /ticket-close

    @app_commands.command(
        name="ticket-close",
        description="Ferme le ticket courant (réservé aux propriétaires du bot).",
    )
    @app_commands.guild_only()
    @app_commands.default_permissions(administrator=True)
    async def ticket_close_cmd(self, interaction: discord.Interaction):
        if not self._can_admin_ticket(interaction):
            await interaction.response.send_message(
                "❌ Réservé aux propriétaires du bot. Le staff peut fermer "
                "le ticket via le bouton « 🔒 Fermer le ticket ».",
                ephemeral=True,
            )
            return
        cfg = _get_guild_cfg(interaction.guild.id)
        ticket = cfg.get("tickets", {}).get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message(
                "❌ Cette commande doit être utilisée à l'intérieur d'un ticket.",
                ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Confirme la fermeture de ce ticket :",
            view=CloseConfirmView(interaction.channel.id, interaction.user.id),
            ephemeral=True,
        )

    @staticmethod
    def _can_close(interaction: discord.Interaction, cfg: dict, ticket: dict) -> bool:
        """Seul le staff (admin/manage_guild OU rôle staff configuré) peut fermer."""
        member = interaction.user
        if not isinstance(member, discord.Member):
            return False
        if member.guild_permissions.administrator or member.guild_permissions.manage_guild:
            return True
        staff_id = cfg.get("staff_role_id")
        if staff_id and any(str(r.id) == str(staff_id) for r in member.roles):
            return True
        return False

    # ====================================================================
    # HANDLERS DE BOUTONS PERSISTANTS
    # ====================================================================

    @staticmethod
    async def handle_open_ticket(interaction: discord.Interaction):
        guild = interaction.guild
        if guild is None or not isinstance(interaction.user, discord.Member):
            return
        cfg = _get_guild_cfg(guild.id)
        if not cfg.get("panel_channel_id") or not cfg.get("staff_role_id"):
            await interaction.response.send_message(
                "❌ Le système de tickets n'est pas configuré. Demande à un admin "
                "d'utiliser `/ticket-setup`.",
                ephemeral=True,
            )
            return

        # Un seul ticket ouvert par membre
        for ch_id, t in (cfg.get("tickets") or {}).items():
            if t.get("status") == "open" and str(t.get("creator_id")) == str(interaction.user.id):
                existing = guild.get_channel(int(ch_id))
                if existing is not None:
                    await interaction.response.send_message(
                        f"⚠️ Tu as déjà un ticket ouvert : {existing.mention}",
                        ephemeral=True,
                    )
                    return

        # Vérifie les horaires d'ouverture (si configurés)
        hours_cfg = cfg.get("hours") or {}
        is_open, reason = _is_within_hours(hours_cfg)
        if not is_open:
            extra = hours_cfg.get("message")
            days_str = _format_days(hours_cfg.get("days") or [])
            embed = discord.Embed(
                title="⏰ Support fermé",
                description=(
                    f"{reason}\n\n"
                    f"**Horaires :** {hours_cfg.get('open','--:--')}–"
                    f"{hours_cfg.get('close','--:--')} ({hours_cfg.get('tz','Europe/Paris')})\n"
                    f"**Jours d'ouverture :** {days_str}"
                    + (f"\n\n💬 {extra}" if extra else "")
                ),
                color=0xf59e0b,
            )
            await interaction.response.send_message(embed=embed, ephemeral=True)
            return

        await interaction.response.defer(ephemeral=True, thinking=True)

        # S'assure de la catégorie
        cog = interaction.client.get_cog("TicketsCog")
        if cog is None:
            await interaction.followup.send(
                "❌ Système de tickets indisponible. Réessaie plus tard.", ephemeral=True,
            )
            return
        category = await cog._ensure_category(guild, cfg)
        if category is None:
            await interaction.followup.send(
                "❌ Je n'ai pas pu créer la catégorie de tickets. Demande à un admin de "
                "vérifier ma permission **Manage Channels**.",
                ephemeral=True,
            )
            return

        staff_role = guild.get_role(int(cfg["staff_role_id"]))
        if staff_role is None:
            await interaction.followup.send(
                "❌ Le rôle staff configuré n'existe plus. Un admin doit relancer `/ticket-setup`.",
                ephemeral=True,
            )
            return

        # Le tier prio est basé sur l'utilisateur (admin d'un serveur Premium payant),
        # pas sur le serveur où le ticket est ouvert (sinon tout le monde sur le
        # serveur de support se retrouverait prioritaire en permanence).
        tier = get_user_customer_tier(interaction.client, interaction.user.id)
        theme = _theme(tier)
        # Numérotation atomique
        async with _STORAGE_LOCK:
            data = _load_storage()
            gcfg = data.get(str(guild.id), {}) or {}
            number = int(gcfg.get("next_number", 1))
            gcfg["next_number"] = number + 1
            data[str(guild.id)] = gcfg
            _save_storage(data)

        username = _safe_username_for_channel(interaction.user.name)
        channel_name = f"{theme['emoji']}-{username}-{number:04d}"

        overwrites = {
            guild.default_role: discord.PermissionOverwrite(view_channel=False),
            guild.me: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, embed_links=True,
                attach_files=True, read_message_history=True, manage_channels=True,
                manage_messages=True,
            ),
            interaction.user: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
                read_message_history=True, embed_links=True,
            ),
            staff_role: discord.PermissionOverwrite(
                view_channel=True, send_messages=True, attach_files=True,
                read_message_history=True, embed_links=True, manage_messages=True,
            ),
        }
        try:
            ticket_channel = await guild.create_text_channel(
                name=channel_name,
                category=category,
                overwrites=overwrites,
                topic=f"Ticket #{number:04d} — {interaction.user} ({tier})",
                reason=f"Ticket ouvert par {interaction.user}",
            )
        except discord.Forbidden:
            await interaction.followup.send(
                "❌ Je n'ai pas la permission `Manage Channels` pour créer le ticket.",
                ephemeral=True,
            )
            return
        except discord.HTTPException as e:
            await interaction.followup.send(
                f"❌ Erreur Discord lors de la création du ticket : {e}", ephemeral=True,
            )
            return

        # Persistance (mutator SYNCHRONE — _update_guild_cfg appelle mutator(cfg)
        # de façon synchrone ; un async def serait silencieusement ignoré)
        def _store(c: dict):
            c.setdefault("tickets", {})
            c["tickets"][str(ticket_channel.id)] = {
                "creator_id": str(interaction.user.id),
                "tier": tier,
                "number": number,
                "opened_at": datetime.now(timezone.utc).isoformat(),
                "claimed_by": None,
                "status": "open",
            }
        await _update_guild_cfg(guild.id, _store)

        # Premier message
        sla_hours = _SLA_HOURS.get(tier)
        if sla_hours:
            sla_line = f"⏱️ **Temps de réponse garanti** : sous {sla_hours} h\n"
        else:
            sla_line = "⏱️ **File d'attente** : Standard *(pas de SLA)*\n"
        embed = discord.Embed(
            title=f"{theme['emoji']} Ticket #{number:04d} — Bienvenue {interaction.user.display_name}",
            description=(
                "Décris ton problème ou ta demande aussi précisément que possible.\n"
                "L'équipe te répondra dès que possible.\n\n"
                f"**Statut du membre** : {theme['label']}\n"
                f"{sla_line}"
                "**Pour fermer** : utilise le bouton 🔒 ci-dessous ou `/ticket-close`."
            ),
            color=theme["color"],
            timestamp=datetime.now(timezone.utc),
        )
        embed.set_footer(text=f"Ouvert par {interaction.user}", icon_url=interaction.user.display_avatar.url)

        # Mention créateur + (si Premium) ping staff
        content_parts = [interaction.user.mention]
        allowed = discord.AllowedMentions(users=[interaction.user], roles=False, everyone=False)
        if theme["ping"]:
            content_parts.append(staff_role.mention)
            allowed = discord.AllowedMentions(users=[interaction.user], roles=[staff_role], everyone=False)
        if tier == TIER_PREMIUM_PLUS:
            content_parts.append("**[PRIORITAIRE]**")

        welcome_msg = await ticket_channel.send(
            content=" ".join(content_parts),
            embed=embed,
            view=TicketActionsView(),
            allowed_mentions=allowed,
        )

        # Sauvegarde l'ID du message d'accueil pour pouvoir l'éditer au moment du claim
        def _store_msg(c: dict):
            c.setdefault("tickets", {})
            t = c["tickets"].get(str(ticket_channel.id))
            if t:
                t["welcome_message_id"] = str(welcome_msg.id)
        await _update_guild_cfg(guild.id, _store_msg)

        # ── Notification d'ouverture dans le canal récap ─────────────────────
        try:
            recap_ch = guild.get_channel(_RECAP_CHANNEL_ID)
            if recap_ch is None:
                try:
                    recap_ch = await guild.fetch_channel(_RECAP_CHANNEL_ID)
                except Exception:
                    recap_ch = None
            if recap_ch is not None:
                _tier_labels_open = {
                    TIER_FREE:         "🆓 Gratuit",
                    TIER_PREMIUM:      "💎 Premium",
                    TIER_PREMIUM_PLUS: "👑 Premium+",
                }
                _tier_colors_open = {
                    TIER_FREE:         0x6b7280,
                    TIER_PREMIUM:      0xf59e0b,
                    TIER_PREMIUM_PLUS: 0xa855f7,
                }
                guild_tier      = get_guild_tier(guild.id)
                open_embed = discord.Embed(
                    title=f"🎫 Nouveau ticket #{number:04d} ouvert",
                    color=_tier_colors_open.get(tier, 0x6b7280),
                    timestamp=datetime.now(timezone.utc),
                )
                open_embed.add_field(name="👤 Membre", value=interaction.user.mention, inline=True)
                open_embed.add_field(name="📌 Canal", value=ticket_channel.mention, inline=True)
                open_embed.add_field(name="🎫 Type de ticket", value=_tier_labels_open.get(tier, tier), inline=True)
                open_embed.add_field(name="💎 Premium serveur", value=_tier_labels_open.get(guild_tier, guild_tier), inline=True)
                open_embed.set_footer(
                    text=f"Serveur : {guild.name}",
                    icon_url=guild.icon.url if guild.icon else None,
                )
                await recap_ch.send(embed=open_embed)
        except Exception as e:
            logger.warning(f"Impossible d'envoyer la notif d'ouverture du ticket : {e}")

        await interaction.followup.send(
            f"✅ Ton ticket est ouvert : {ticket_channel.mention}",
            ephemeral=True,
        )

    @staticmethod
    async def handle_claim_ticket(interaction: discord.Interaction):
        guild = interaction.guild
        cfg = _get_guild_cfg(guild.id)
        ticket = cfg.get("tickets", {}).get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("❌ Ce canal n'est pas un ticket actif.", ephemeral=True)
            return
        # Seul le staff peut claim
        member = interaction.user
        staff_id = cfg.get("staff_role_id")
        is_staff = isinstance(member, discord.Member) and (
            member.guild_permissions.administrator or
            member.guild_permissions.manage_guild or
            (staff_id and any(str(r.id) == str(staff_id) for r in member.roles))
        )
        if not is_staff:
            await interaction.response.send_message(
                "❌ Seul le staff peut prendre en charge un ticket.", ephemeral=True,
            )
            return
        if ticket.get("claimed_by"):
            await interaction.response.send_message(
                f"⚠️ Ce ticket est déjà pris en charge par <@{ticket['claimed_by']}>.",
                ephemeral=True,
            )
            return

        def _claim(c: dict):
            c.setdefault("tickets", {})
            t = c["tickets"].get(str(interaction.channel.id))
            if t:
                t["claimed_by"] = str(member.id)
                t["claimed_at"] = datetime.now(timezone.utc).isoformat()
        await _update_guild_cfg(guild.id, _claim)

        # Édite le message d'accueil : ajoute "Pris en charge" à l'embed
        # et désactive le bouton "Prendre en charge" (le bouton "Fermer" reste actif).
        welcome_msg_id = ticket.get("welcome_message_id")
        if welcome_msg_id:
            try:
                welcome_msg = await interaction.channel.fetch_message(int(welcome_msg_id))
                new_embed = None
                if welcome_msg.embeds:
                    e = welcome_msg.embeds[0]
                    new_embed = discord.Embed.from_dict(e.to_dict())
                    new_embed.add_field(
                        name="👤 Pris en charge par",
                        value=f"{member.mention}\n*depuis {discord.utils.format_dt(datetime.now(timezone.utc), 'R')}*",
                        inline=False,
                    )
                # Construit une nouvelle vue : claim désactivé + libellé mis à jour
                new_view = discord.ui.View(timeout=None)
                claim_btn = discord.ui.Button(
                    label=f"Pris en charge par {member.display_name}",
                    emoji="👤",
                    style=discord.ButtonStyle.success,
                    custom_id="miaouscle:ticket:claim",
                    disabled=True,
                )
                close_btn = discord.ui.Button(
                    label="Fermer le ticket",
                    emoji="🔒",
                    style=discord.ButtonStyle.danger,
                    custom_id="miaouscle:ticket:close",
                )

                async def _close_cb(i: discord.Interaction):
                    await TicketsCog.handle_close_ticket(i)
                close_btn.callback = _close_cb  # type: ignore[assignment]

                new_view.add_item(claim_btn)
                new_view.add_item(close_btn)

                await welcome_msg.edit(
                    embed=new_embed if new_embed else welcome_msg.embeds[0],
                    view=new_view,
                )
            except (discord.NotFound, discord.HTTPException) as e:
                logger.warning(f"Édition message d'accueil ticket échouée : {e}")

        await interaction.response.send_message(
            f"👤 {member.mention} prend en charge ce ticket.",
        )

    @staticmethod
    async def handle_close_ticket(interaction: discord.Interaction):
        guild = interaction.guild
        cfg = _get_guild_cfg(guild.id)
        ticket = cfg.get("tickets", {}).get(str(interaction.channel.id))
        if not ticket:
            await interaction.response.send_message("❌ Ce canal n'est pas un ticket actif.", ephemeral=True)
            return
        if not TicketsCog._can_close(interaction, cfg, ticket):
            await interaction.response.send_message(
                "❌ Seul le staff peut le fermer.", ephemeral=True,
            )
            return
        await interaction.response.send_message(
            "Confirme la fermeture de ce ticket :",
            view=CloseConfirmView(interaction.channel.id, interaction.user.id),
            ephemeral=True,
        )

    @staticmethod
    async def do_close_ticket(guild: discord.Guild, channel_id: int, closed_by: discord.Member):
        ch = guild.get_channel(channel_id)
        if ch is None:
            return

        # Idempotence : transition atomique open → closing. Si déjà closing/closed,
        # on sort sans rien faire (évite double envoi de message + double delete
        # quand deux confirmations arrivent en parallèle).
        already_closing = {"v": False}

        def _mark(c: dict):
            t = c.get("tickets", {}).get(str(channel_id))
            if not t:
                already_closing["v"] = True
                return
            if t.get("status") in ("closing", "closed"):
                already_closing["v"] = True
                return
            t["status"] = "closing"
            t["closed_at"] = datetime.now(timezone.utc).isoformat()
            t["closed_by"] = str(closed_by.id)
        await _update_guild_cfg(guild.id, _mark)

        if already_closing["v"]:
            return

        # Lire les données du ticket AVANT suppression du canal
        ticket_data = _get_guild_cfg(guild.id).get("tickets", {}).get(str(channel_id), {})

        try:
            await ch.send(
                embed=discord.Embed(
                    description=f"🔒 Ticket fermé par {closed_by.mention}. "
                                f"Suppression du canal dans 5 secondes…",
                    color=0xef4444,
                )
            )
            await asyncio.sleep(5)
            await ch.delete(reason=f"Ticket fermé par {closed_by}")
        except (discord.Forbidden, discord.HTTPException) as e:
            logger.warning(f"Impossible de supprimer le canal de ticket {channel_id} : {e}")
        finally:
            # Marque comme définitivement fermé (le canal peut avoir disparu)
            def _final(c: dict):
                t = c.get("tickets", {}).get(str(channel_id))
                if t:
                    t["status"] = "closed"
            try:
                await _update_guild_cfg(guild.id, _final)
            except Exception:
                pass

        # ── Embed récapitulatif envoyé après fermeture ──────────────────────
        try:
            recap_ch = guild.get_channel(_RECAP_CHANNEL_ID)
            if recap_ch is None:
                try:
                    recap_ch = await guild.fetch_channel(_RECAP_CHANNEL_ID)
                except Exception:
                    recap_ch = None

            if recap_ch is not None:
                # Infos du ticket
                ticket_num   = ticket_data.get("number", "?")
                creator_id   = ticket_data.get("creator_id")
                claimed_id   = ticket_data.get("claimed_by")
                opened_at_s  = ticket_data.get("opened_at")
                ticket_tier  = ticket_data.get("tier", TIER_FREE)

                # Résolution des membres
                creator_mention = f"<@{creator_id}>" if creator_id else "*(inconnu)*"
                claimed_mention = f"<@{claimed_id}>" if claimed_id else "*(personne)*"

                # Durée du ticket
                duration_str = "—"
                if opened_at_s:
                    try:
                        opened_dt = datetime.fromisoformat(opened_at_s)
                        if opened_dt.tzinfo is None:
                            opened_dt = opened_dt.replace(tzinfo=timezone.utc)
                        delta = datetime.now(timezone.utc) - opened_dt
                        total_s = int(delta.total_seconds())
                        h, rem  = divmod(total_s, 3600)
                        m, s    = divmod(rem, 60)
                        if h:
                            duration_str = f"{h}h {m}m {s}s"
                        elif m:
                            duration_str = f"{m}m {s}s"
                        else:
                            duration_str = f"{s}s"
                    except Exception:
                        pass

                # Statut Premium du serveur
                guild_tier = get_guild_tier(guild.id)
                _tier_labels = {
                    TIER_FREE:         "🆓 Gratuit",
                    TIER_PREMIUM:      "💎 Premium",
                    TIER_PREMIUM_PLUS: "👑 Premium+",
                }
                guild_tier_label  = _tier_labels.get(guild_tier,  guild_tier)
                ticket_tier_label = _tier_labels.get(ticket_tier, ticket_tier)

                # Couleur selon tier du ticket
                _tier_colors = {
                    TIER_FREE:         0x6b7280,
                    TIER_PREMIUM:      0xf59e0b,
                    TIER_PREMIUM_PLUS: 0xa855f7,
                }
                color = _tier_colors.get(ticket_tier, 0x6b7280)

                embed = discord.Embed(
                    title=f"📋 Ticket #{ticket_num:04d} — Récapitulatif",
                    color=color,
                    timestamp=datetime.now(timezone.utc),
                )
                embed.add_field(name="👤 Membre", value=creator_mention, inline=True)
                embed.add_field(name="🛠️ Pris en charge par", value=claimed_mention, inline=True)
                embed.add_field(name="🔒 Fermé par", value=closed_by.mention, inline=True)
                embed.add_field(name="⏱️ Durée", value=duration_str, inline=True)
                embed.add_field(name="🎫 Type de ticket", value=ticket_tier_label, inline=True)
                embed.add_field(name="💎 Premium serveur", value=guild_tier_label, inline=True)
                embed.set_footer(
                    text=f"Serveur : {guild.name}",
                    icon_url=guild.icon.url if guild.icon else None,
                )
                await recap_ch.send(embed=embed)
        except Exception as e:
            logger.warning(f"Impossible d'envoyer le récap du ticket {channel_id} : {e}")


async def setup(bot: commands.Bot):
    await bot.add_cog(TicketsCog(bot))
