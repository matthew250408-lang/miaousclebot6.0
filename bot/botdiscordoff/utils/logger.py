
import discord
import logging
from datetime import datetime

BUG_REPORT_CHANNEL_ID = 1440720849375068170  # Canal central du bot - tout se log ici

async def send_notification(bot, bug_type: str, title: str, description: str, severity: str = "warning", user = None, guild = None, command = None):
    """
    Envoie une notification au salon central du bot
    C'est la console centrale du bot - tout les changements y sont loggés!
    
    Args:
        bot: Instance du bot Discord
        bug_type: Type (BUG, MODIFICATION, INFO, COGS_LOADING, CONFIG_CHANGE, SUCCESS, ERROR)
        title: Titre court
        description: Description détaillée
        severity: success=vert, warning=orange, error=rouge
        user: Utilisateur qui a exécuté la commande (optionnel)
        guild: Serveur du contexte (optionnel)
        command: Nom de la commande exécutée (optionnel)
    """
    try:
        print(f"🔍 Tentative d'envoi de notification: {title}")
        
        # Vérifier que le bot est prêt
        if not bot.user:
            print(f"❌ Bot pas prêt - user est None")
            return
        
        # Récupérer le canal
        channel = bot.get_channel(BUG_REPORT_CHANNEL_ID)
        if not channel:
            print(f"⚠️ Canal {BUG_REPORT_CHANNEL_ID} non trouvé en cache, tentative fetch...")
            try:
                channel = await bot.fetch_channel(BUG_REPORT_CHANNEL_ID)
                print(f"✅ Canal fetchê avec succès")
            except Exception as fetch_error:
                print(f"❌ Impossible de fetch le canal {BUG_REPORT_CHANNEL_ID}: {fetch_error}")
                return
        
        colors = {
            "success": 0x00ff00,
            "warning": 0xffaa00,
            "error": 0xff0000
        }
        
        emojis = {
            "BUG": "🐛",
            "MODIFICATION": "✨",
            "INFO": "ℹ️",
            "SUCCESS": "✅",
            "ERROR": "❌",
            "COGS_LOADING": "📦",
            "CONFIG_CHANGE": "⚙️",
            "BOT_START": "🚀",
            "BOT_STOP": "⛔"
        }
        
        # Vérifier et limiter la taille de la description
        if len(description) > 4000:
            description = description[:3997] + "..."
        
        embed = discord.Embed(
            title=f"{emojis.get(bug_type, '📋')} {title}",
            description=description,
            color=colors.get(severity, 0xffaa00),
            timestamp=datetime.utcnow()
        )
        
        # Ajouter les infos de contexte si disponibles
        if command and len(embed) < 5500:
            embed.add_field(
                name="⌨️ Commande Exécutée",
                value=f"`/{command}`",
                inline=False
            )
        
        if user and len(embed) < 5500:
            embed.add_field(
                name="👤 Utilisateur",
                value=f"{user.mention}\n`{user.name}#{user.discriminator}`",
                inline=True
            )
        
        if guild and len(embed) < 5500:
            embed.add_field(
                name="🖥️ Serveur",
                value=f"{guild.name}\n`ID: {guild.id}`",
                inline=True
            )
        
        embed.set_footer(text="🐱 Central Miaouscle Bot")
        
        await channel.send(embed=embed)
        print(f"✅ Notification envoyée avec succès: {bug_type} - {title}")
        
    except Exception as e:
        print(f"❌ Erreur lors de l'envoi de notification: {e}")
        import traceback
        traceback.print_exc()

# Alias pour compatibilité
async def send_bug_notification(bot, bug_type: str, title: str, description: str, severity: str = "warning", user = None, guild = None, command = None):
    """Wrapper pour compatibilité avec l'ancien code"""
    await send_notification(bot, bug_type, title, description, severity, user, guild, command)

async def send_bot_news(bot, titre: str, description: str):
    """
    Envoie une news du bot directement au salon central
    Utilise cette fonction pour annoncer les modifications du bot
    
    Args:
        bot: Instance du bot Discord
        titre: Titre de la news (ex: "Nouvelle commande ajoutée")
        description: Description détaillée des changements
    """
    await send_notification(bot, "SUCCESS", f"📰 {titre}", description, "success")
