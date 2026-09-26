import discord
import traceback
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

async def send_error(interaction: discord.Interaction, error: Exception, bot=None):
    """
    Envoie un message d'erreur clair à l'utilisateur,
    affiche l'erreur complète dans la console,
    et l'envoie dans le salon de logs du serveur si configuré.
    """

    # ------------------------
    # 🔍 Trace complète dans la console
    # ------------------------
    traceback_str = ''.join(traceback.format_exception(type(error), error, error.__traceback__))
    logger.error(f"❌ Erreur capturée dans une commande :\n{traceback_str}")

    # ------------------------
    # 💬 Embed utilisateur
    # ------------------------
    embed = discord.Embed(
        title="❌ Une erreur est survenue",
        description=(
            f"Une erreur s’est produite pendant l’exécution de la commande.\n\n"
            f"**Type :** `{type(error).__name__}`\n"
            f"**Détails :** `{str(error)}`"
        ),
        color=discord.Color.red(),
        timestamp=datetime.utcnow()
    )
    embed.set_footer(text="Contactez un administrateur si le problème persiste.")

    # ------------------------
    # 📤 Envoi à l'utilisateur
    # ------------------------
    try:
        if interaction.response.is_done():
            await interaction.followup.send(embed=embed, ephemeral=True)
        else:
            await interaction.response.send_message(embed=embed, ephemeral=True)
    except Exception as e:
        logger.warning(f"⚠️ Impossible d'envoyer le message d'erreur à l'utilisateur : {e}")

    # ------------------------
    # 🧾 Envoi dans le salon de logs (si configuré)
    # ------------------------
    if not bot or not interaction.guild:
        return  # pas de logs hors serveur

    try:
        from utils.database import load_config
        config = load_config(interaction.guild.id)
        log_channel_id = config.get("log_channel")

        if not log_channel_id:
            return  # aucun salon de logs défini

        channel = bot.get_channel(int(log_channel_id))
        if not channel:
            return

        log_embed = discord.Embed(
            title="⚠️ Erreur capturée",
            description=f"```{traceback_str[:1900]}```",
            color=discord.Color.dark_red(),
            timestamp=datetime.utcnow()
        )
        log_embed.add_field(name="Serveur", value=f"{interaction.guild.name} (`{interaction.guild.id}`)", inline=False)
        log_embed.add_field(name="Utilisateur", value=f"{interaction.user} (`{interaction.user.id}`)", inline=False)
        log_embed.add_field(name="Commande", value=f"`/{interaction.command.name if interaction.command else 'inconnue'}`", inline=False)
        log_embed.set_footer(text="Miaouscle Bot - Journal d’erreurs")

        await channel.send(embed=log_embed)

    except Exception as log_err:
        logger.error(f"Erreur lors de l'envoi du log dans le salon : {log_err}")
