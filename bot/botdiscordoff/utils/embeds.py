import discord

def create_embed(title, description, color, thumbnail=None, footer=None):
    """Créer un embed Discord standardisé"""
    embed = discord.Embed(
        title=title,
        description=description,
        color=color
    )

    if thumbnail:
        embed.set_thumbnail(url=thumbnail)

    if footer:
        embed.set_footer(text=footer)
    else:
        embed.set_footer(text="Miaouscle Bot")

    return embed

def create_success_embed(title, description):
    """Créer un embed de succès"""
    return create_embed(title, description, 0x00ff00)

def create_error_embed(title, description):
    """Créer un embed d'erreur"""
    return create_embed(title, description, 0xff0000)

def create_warning_embed(title, description):
    """Créer un embed d'avertissement"""
    return create_embed(title, description, 0xffaa00)

def create_info_embed(title, description):
    """Créer un embed d'information"""
    return create_embed(title, description, 0x0099ff)
