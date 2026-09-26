# cogs/absence_staff.py
"""
Module de commande /absence_staff pour la gestion des absences du staff.
- Date de début automatique (aujourd'hui)
- L'utilisateur rentre: type, date de fin, raison
- Affiche le rôle le plus haut avec couleur personnalisée
- Modifie le message avec ✅ quand l'absence se termine
"""
import discord
from discord.ext import commands, tasks
from discord import app_commands, Interaction, ui
import json
import os
import re
from datetime import datetime, timezone
from typing import Optional

# Configuration
TARGET_GUILD_ID = 1188506879052238988
ABSENCES_CHANNEL_ID = 1308539884528472075
STAFF_ROLE_IDS = {1188508232470245427, 1188507134644731915}
OWNER_ID = 653038666625785866


class ConfirmAbsenceView(ui.View):
    """View ephemeral pour confirmer la fin de l'absence."""

    def __init__(self, cog: "AbsenceStaffCog", absence_id: str, user_id: int, timeout: float = 180.0):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.absence_id = absence_id
        self.user_id = user_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Vérifier que seul l'auteur peut utiliser les boutons."""
        if interaction.user.id != self.user_id:
            await interaction.response.send_message(
                "❌ Tu ne peux pas utiliser ce bouton.",
                ephemeral=True,
            )
            return False
        return True

    @ui.button(label="✅ Confirmer", style=discord.ButtonStyle.green)
    async def confirm_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Bouton de confirmation."""
        await interaction.response.defer()
        
        try:
            absences = self.cog.load_absences()
            
            if self.absence_id not in absences:
                await interaction.followup.send(
                    "❌ Absence non trouvée.",
                    ephemeral=True,
                )
                return
            
            absence_data = absences[self.absence_id]
            guild = self.cog.bot.get_guild(absence_data["guild_id"])
            
            if guild:
                channel = guild.get_channel(absence_data["channel_id"])
                if channel:
                    try:
                        message = await channel.fetch_message(absence_data["message_id"]) # type: ignore
                        
                        # Modifier l'embed avec ✅
                        if message.embeds:
                            embed = message.embeds[0]
                            user_name = absence_data.get("user_name", "Utilisateur")
                            embed.title = f"✅ Absence de @{user_name} terminée"
                            embed.color = 0x00ff00
                            embed.set_footer(text=f"✅ Terminée • Equipe miaoscule")
                            await message.edit(embed=embed, view=None)
                            # Ajouter la réaction ✅
                            await message.add_reaction("✅")
                    except Exception as e:
                        print(f"❌ Erreur modification message: {e}")
            
            # Marquer comme notifiée
            absence_data["notified"] = True
            absences[self.absence_id] = absence_data
            self.cog.save_absences(absences)
            
            # Envoyer un DM privé à l'utilisateur
            try:
                user = await self.cog.bot.fetch_user(absence_data["user_id"])
                if user:
                    embed = discord.Embed(
                        title="✅ Absence terminée",
                        description=f"Ton absence sur le serveur staff est désormais terminée.",
                        color=0x00ff00,
                        timestamp=datetime.now(timezone.utc)
                    )
                    embed.add_field(name="📋 Raison", value=absence_data['raison'], inline=False)
                    embed.add_field(name="👑 Rôle", value=absence_data['role_name'], inline=True)
                    embed.set_footer(text="Bon retour parmi nous ! • Miaouscle Bot")
                    await user.send(embed=embed)
            except Exception as e:
                print(f"❌ Erreur envoi DM: {e}")
            
            await interaction.followup.send(
                "✅ Absence terminée avec succès!",
                ephemeral=True,
            )
        except Exception as e:
            print(f"❌ Erreur confirmation absence: {e}")
            await interaction.followup.send(
                f"❌ Erreur: {str(e)}",
                ephemeral=True,
            )

    @ui.button(label="❌ Annuler", style=discord.ButtonStyle.red)
    async def cancel_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Bouton d'annulation."""
        await interaction.response.send_message(
            "❌ Annulé.",
            ephemeral=True,
        )


class FinishAbsenceButtonView(ui.View):
    """View avec bouton 'Finir maintenant' pour terminer l'absence."""

    def __init__(self, cog: "AbsenceStaffCog", absence_id: str, user_id: int, timeout: float = None):
        super().__init__(timeout=timeout)
        self.cog = cog
        self.absence_id = absence_id
        self.user_id = user_id

    async def interaction_check(self, interaction: Interaction) -> bool:
        """Vérifier que seul l'auteur peut utiliser le bouton."""
        if interaction.user.id != self.user_id:
            # Message d'erreur ephemeral
            try:
                await interaction.response.defer(ephemeral=True)
                absences = self.cog.load_absences()
                absence_data = absences.get(self.absence_id, {})
                absence_author = absence_data.get("user_name", "Inconnu")
                await interaction.followup.send(
                    f"❌ Tu n'es pas autorisé! Cette absence appartient à **{absence_author}**.",
                    ephemeral=True,
                )
            except Exception as e:
                print(f"❌ Erreur vérification bouton: {e}")
            return False
        return True

    @ui.button(label="⏹️ Finir maintenant", style=discord.ButtonStyle.blurple)
    async def finish_button(self, interaction: Interaction, button: ui.Button) -> None:
        """Affiche une confirmation ephemeral."""
        # Créer la view de confirmation ephemeral
        confirm_view = ConfirmAbsenceView(self.cog, self.absence_id, self.user_id)
        
        await interaction.response.send_message(
            "🔔 Confirmer la fin de l'absence?",
            view=confirm_view,
            ephemeral=True,
        )


class AbsenceStaffCog(commands.Cog):
    """Cog pour la commande /absence_staff."""

    def __init__(self, bot: commands.Bot):
        self.bot = bot
        self.absences_file = 'web/data/staff_absences.json'
        os.makedirs('web/data', exist_ok=True)
        self.check_absence_end.start()

    def load_absences(self) -> dict:
        """Charge les absences depuis le fichier JSON."""
        try:
            if os.path.exists(self.absences_file):
                with open(self.absences_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            return {}
        except Exception as e:
            print(f"❌ Erreur chargement absences: {e}")
            return {}

    def save_absences(self, absences: dict) -> None:
        """Sauvegarde les absences dans le fichier JSON."""
        try:
            os.makedirs('web/data', exist_ok=True)
            with open(self.absences_file, 'w', encoding='utf-8') as f:
                json.dump(absences, f, indent=2, ensure_ascii=False)
        except Exception as e:
            print(f"❌ Erreur sauvegarde absences: {e}")

    def _validate_date_format(self, date_str: str) -> bool:
        """Valide le format JJ/MM/YYYY."""
        pattern = r'^\d{2}/\d{2}/\d{4}$'
        if not re.match(pattern, date_str):
            return False
        
        try:
            day, month, year = date_str.split('/')
            day_int = int(day)
            month_int = int(month)
            year_int = int(year)
            
            if not (1 <= day_int <= 31 and 1 <= month_int <= 12 and year_int >= 2000):
                return False
            return True
        except (ValueError, IndexError):
            return False

    def get_highest_colored_role(self, member: discord.Member) -> Optional[discord.Role]:
        """Récupère le rôle le plus haut avec couleur personnalisée."""
        highest_role = None
        highest_position = -1
        
        for role in member.roles:
            # Couleur personnalisée ET pas un rôle système
            if role.color != discord.Color.default() and not role.managed:
                if role.position > highest_position:
                    highest_position = role.position
                    highest_role = role
        
        return highest_role

    def format_date_display(self, date: datetime) -> str:
        """Formate la date pour l'affichage (JJ/MM/YYYY)."""
        return date.strftime("%d/%m/%Y")
    
    def has_active_absence(self, user_id: int) -> bool:
        """Vérifie si l'utilisateur a une absence active."""
        absences = self.load_absences()
        now = datetime.now(timezone.utc)
        
        for absence_data in absences.values():
            if absence_data.get("user_id") == user_id and not absence_data.get("notified"):
                end_date = datetime.fromisoformat(absence_data["end_date"])
                if now < end_date:
                    return True
        return False

    @app_commands.command(
        name="absence_staff",
        description="Déclarer une absence du staff",
    )
    @app_commands.guild_only()
    @app_commands.describe(
        end_date="Date de fin (JJ/MM/YYYY)",
        raison="Motif de l'absence",
    )
    async def absence_staff(
        self,
        interaction: Interaction,
        end_date: str,
        raison: str,
    ) -> None:
        """Déclare une absence du staff."""
        await interaction.response.defer(ephemeral=True)

        try:
            # Vérifier le serveur
            if interaction.guild_id != TARGET_GUILD_ID:
                await interaction.followup.send(
                    "⛔ Cette commande n'est disponible que sur le serveur staff.",
                    ephemeral=True,
                )
                return

            # Vérifier les rôles requis
            if isinstance(interaction.user, discord.Member):
                has_role = any(role.id in STAFF_ROLE_IDS for role in interaction.user.roles)
                if not has_role and interaction.user.id != OWNER_ID:
                    await interaction.followup.send(
                        "⛔ Tu dois avoir l'un des rôles staff autorisés.",
                        ephemeral=True,
                    )
                    return
            else:
                await interaction.followup.send(
                    "⛔ Cette commande ne fonctionne que sur un serveur.",
                    ephemeral=True,
                )
                return

            # Valider la date de fin - Format: JJ/MM/YYYY
            if not self._validate_date_format(end_date):
                await interaction.followup.send(
                    "❌ Format de date invalide. Format requis: JJ/MM/YYYY (ex: 30/11/2025)",
                    ephemeral=True,
                )
                return

            # Vérifier si l'utilisateur a déjà une absence active
            if self.has_active_absence(interaction.user.id):
                await interaction.followup.send(
                    "⚠️ Tu as déjà une absence en cours. Termine-la d'abord avec le bouton '⏹️ Finir maintenant' avant d'en déclarer une nouvelle.",
                    ephemeral=True,
                )
                return
            
            # Date de début = aujourd'hui (auto-détectée)
            start = datetime.now(timezone.utc).replace(hour=0, minute=0, second=0, microsecond=0)
            
            # Parser la date de fin
            try:
                # Créer la date à minuit du jour sélectionné
                end = datetime.strptime(end_date, "%d/%m/%Y").replace(tzinfo=timezone.utc)
            except ValueError:
                await interaction.followup.send(
                    "❌ Date invalide. Utilise le format: JJ/MM/YYYY",
                    ephemeral=True,
                )
                return

            if start >= end:
                await interaction.followup.send(
                    "❌ La date de fin doit être après aujourd'hui.",
                    ephemeral=True,
                )
                return

            # Récupérer le serveur et l'auteur (interaction.user)
            guild = interaction.guild
            if not guild:
                await interaction.followup.send("❌ Erreur: serveur non trouvé.", ephemeral=True)
                return

            # Récupérer le member qui utilise la commande
            member = interaction.user
            if not isinstance(member, discord.Member):
                member = await guild.fetch_member(interaction.user.id)
                if not member:
                    await interaction.followup.send(
                        "❌ Erreur: impossible de récupérer ton profil.",
                        ephemeral=True,
                    )
                    return

            # Récupérer le rôle le plus haut avec couleur
            highest_role = self.get_highest_colored_role(member)
            if not highest_role:
                await interaction.followup.send(
                    f"❌ Tu n'as pas de rôle staff avec couleur personnalisée.",
                    ephemeral=True,
                )
                return

            # Créer l'embed avec le style de la photo
            embed = discord.Embed(
                title="Absence déclarée",
                color=0x2F3136,
            )
            
            # Pseudo avec ID
            embed.add_field(
                name="👤 Pseudo :",
                value=f"{member.mention} – ({member.id})",
                inline=False,
            )
            
            # Rôle le plus élevé (avec couleur du rôle)
            embed.add_field(
                name="👑 Rôle le plus élevé :",
                value=highest_role.mention,
                inline=False,
            )
            
            # Fin de l'absence - Discord Timestamp (juste la date)
            end_timestamp = int(end.timestamp())
            embed.add_field(
                name="⏰ Fin :",
                value=f"<t:{end_timestamp}:D>",
                inline=False,
            )
            
            # Raison
            embed.add_field(
                name="📝 Raison :",
                value=raison,
                inline=False,
            )
            
            embed.set_thumbnail(url=member.avatar.url if member.avatar else None)
            embed.set_footer(text=f"Equipe miaoscule • Absence staff")

            # Envoyer dans le canal d'absences
            logs_channel = guild.get_channel(ABSENCES_CHANNEL_ID)
            if not logs_channel:
                await interaction.followup.send(
                    f"❌ Canal <#{ABSENCES_CHANNEL_ID}> introuvable.",
                    ephemeral=True,
                )
                return

            try:
                # Créer la view avec juste le bouton "Finir maintenant"
                absence_id = f"{member.id}_{int(start.timestamp())}"
                view = FinishAbsenceButtonView(self, absence_id, member.id)
                message = await logs_channel.send(embed=embed, view=view)
            except discord.Forbidden:
                await interaction.followup.send(
                    f"❌ Je n'ai pas la permission d'envoyer dans <#{ABSENCES_CHANNEL_ID}>.",
                    ephemeral=True,
                )
                return

            # Sauvegarder l'absence
            absences = self.load_absences()
            absences[absence_id] = {
                "user_id": member.id,
                "user_name": member.name,
                "start_date": start.isoformat(),
                "end_date": end.isoformat(),
                "raison": raison,
                "role_name": highest_role.name,
                "guild_id": guild.id,
                "message_id": message.id,
                "channel_id": logs_channel.id,
                "created_at": datetime.now(timezone.utc).isoformat(),
                "notified": False,
            }
            self.save_absences(absences)

            await interaction.followup.send(
                f"✅ Absence enregistrée",
                ephemeral=True,
            )

        except Exception as e:
            print(f"❌ Erreur /absence_staff: {e}")
            await interaction.followup.send(
                f"❌ Erreur: {str(e)}",
                ephemeral=True,
            )

    @tasks.loop(hours=1)
    async def check_absence_end(self) -> None:
        """Vérifie si les absences se terminent."""
        try:
            absences = self.load_absences()
            now = datetime.now(timezone.utc)
            updated = False

            for absence_id, absence_data in list(absences.items()):
                if absence_data.get("notified"):
                    continue

                end_date = datetime.fromisoformat(absence_data["end_date"])

                if now >= end_date:
                    guild = self.bot.get_guild(absence_data.get("guild_id", TARGET_GUILD_ID))
                    if guild:
                        try:
                            # Récupérer le message original
                            channel_id = absence_data.get("channel_id", ABSENCES_CHANNEL_ID)
                            channel = guild.get_channel(channel_id)
                            if channel:
                                try:
                                    message = await channel.fetch_message(absence_data["message_id"]) # type: ignore
                                    
                                    # Modifier l'embed avec ✅
                                    if message.embeds:
                                        embed = message.embeds[0]
                                        user_name = absence_data.get("user_name", "Utilisateur")
                                        embed.title = f"✅ Absence de @{user_name} terminée"
                                        embed.color = 0x00ff00
                                        embed.set_footer(text=f"✅ Terminée • Equipe miaoscule")
                                        await message.edit(embed=embed, view=None)
                                        # Ajouter la réaction ✅
                                        await message.add_reaction("✅")
                                    
                                    # Envoyer un DM privé à l'utilisateur
                                    try:
                                        user = await self.bot.fetch_user(absence_data["user_id"])
                                        if user:
                                            embed = discord.Embed(
                                                title="✅ Absence terminée",
                                                description=f"Ton absence sur le serveur staff est désormais terminée.",
                                                color=0x00ff00,
                                                timestamp=datetime.now(timezone.utc)
                                            )
                                            embed.add_field(name="📋 Raison", value=absence_data['raison'], inline=False)
                                            embed.add_field(name="👑 Rôle", value=absence_data['role_name'], inline=True)
                                            embed.set_footer(text="Bon retour parmi nous ! • Miaouscle Bot")
                                            await user.send(embed=embed)
                                    except Exception as e:
                                        print(f"❌ Erreur envoi DM: {e}")
                                except Exception as e:
                                    print(f"❌ Erreur modification message: {e}")

                            absence_data["notified"] = True
                            updated = True
                        except Exception as e:
                            print(f"❌ Erreur fin absence: {e}")

            if updated:
                self.save_absences(absences)

        except Exception as e:
            print(f"❌ Erreur check_absence_end: {e}")

    @check_absence_end.before_loop
    async def before_check_absence_end(self) -> None:
        """Attendre que le bot soit prêt."""
        await self.bot.wait_until_ready()


async def setup(bot: commands.Bot) -> None:
    """Charge le cog."""
    await bot.add_cog(AbsenceStaffCog(bot))
