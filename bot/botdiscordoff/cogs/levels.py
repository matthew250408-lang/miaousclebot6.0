import discord
from discord.ext import commands
import asyncio
import random
import json
import os
from datetime import datetime, timezone
from PIL import Image, ImageDraw, ImageFont
import aiohttp
import io
from utils.translations import get_text
from utils.premium import has_premium, TIER_PREMIUM


def _hex_to_rgb(value, default):
    """Convertit '#RRGGBB' (ou 'RRGGBB') en tuple RGB. Retourne `default` si invalide."""
    if not value or not isinstance(value, str):
        return default
    value = value.strip().lstrip("#")
    if len(value) != 6:
        return default
    try:
        return (int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16))
    except ValueError:
        return default


def _load_server_config_sync(guild_id: int) -> dict:
    """Charge la config d'un serveur depuis le store persistant (PostgreSQL avec
    fallback JSON). Appelé via to_thread depuis l'event loop Discord."""
    try:
        from utils.server_config_store import load_one
        cfg = load_one(guild_id)
        return cfg if isinstance(cfg, dict) else {}
    except Exception:
        return {}


async def _load_server_config_async(guild_id: int) -> dict:
    """Wrapper async pour ne pas bloquer l'event loop."""
    return await asyncio.to_thread(_load_server_config_sync, guild_id)

# ------------------------
# Utilitaires
# ------------------------
def get_xp_needed(level: int) -> int:
    return (level + 1) * 100

def load_data(guild_id: str) -> dict:
    path = f"data/levels_{guild_id}.json"
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read().strip()
            if not content:
                return {}
            return json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}

def save_data(guild_id: str, data: dict):
    path = f"data/levels_{guild_id}.json"
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=4, ensure_ascii=False)

def process_xp_gain(user_data: dict) -> tuple[bool, int, int, int]:
    """Vérifie et traite la montée de niveau. Retourne (leveled_up, new_level, xp, xp_needed)"""
    leveled_up = False
    current_level = user_data["level"]
    xp_needed = get_xp_needed(current_level)

    while user_data["xp"] >= xp_needed:
        user_data["xp"] -= xp_needed
        user_data["level"] += 1
        current_level = user_data["level"]
        xp_needed = get_xp_needed(current_level)
        leveled_up = True

    return leveled_up, current_level, user_data["xp"], xp_needed

# ------------------------
# Génération carte PNG
# ------------------------
async def generate_rank_image(
    member: discord.Member,
    level: int,
    xp: int,
    xp_needed: int,
    progress: float,
    guild_id: int = 0,
    premium_opts: dict | None = None,
) -> discord.File:
    """
    Génère la carte de niveau. Si `premium_opts` est fourni (serveur Premium),
    utilise les couleurs et l'image de fond personnalisées :
      - rank_bg_color           (#RRGGBB)
      - rank_bar_start_color    (#RRGGBB)
      - rank_bar_end_color      (#RRGGBB)
      - rank_text_color         (#RRGGBB)
      - rank_bg_image           (URL d'une image)
    """
    width, height = 2000, 700
    opts = premium_opts or {}

    bg_color = _hex_to_rgb(opts.get("rank_bg_color"), (26, 27, 31))
    text_color = _hex_to_rgb(opts.get("rank_text_color"), (255, 255, 255))
    bar_bg = (54, 57, 63)
    bar_start = _hex_to_rgb(opts.get("rank_bar_start_color"), (88, 101, 242))
    bar_end = _hex_to_rgb(opts.get("rank_bar_end_color"), (255, 215, 0))
    bg_image_url = (opts.get("rank_bg_image") or "").strip() or None

    # Avatar utilisateur
    try:
        async with aiohttp.ClientSession() as session:
            async with session.get(str(member.display_avatar.url)) as resp:
                avatar_bytes = await resp.read()
        avatar = Image.open(io.BytesIO(avatar_bytes)).convert("RGBA").resize((250, 250))
    except Exception:
        avatar = Image.new("RGBA", (250, 250), (120, 120, 120, 255))

    mask = Image.new("L", (250, 250), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, 250, 250), fill=255)

    # Fond : image custom (Premium) ou couleur unie
    img = None
    if bg_image_url:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(bg_image_url, timeout=aiohttp.ClientTimeout(total=4)) as resp:
                    if resp.status == 200:
                        bg_bytes = await resp.read()
                        bg_img = Image.open(io.BytesIO(bg_bytes)).convert("RGBA")
                        bg_img = bg_img.resize((width, height))
                        # Voile sombre pour garder la lisibilité du texte
                        overlay = Image.new("RGBA", (width, height), (0, 0, 0, 110))
                        img = Image.alpha_composite(bg_img, overlay)
        except Exception:
            img = None

    if img is None:
        img = Image.new("RGBA", (width, height), bg_color + (255,))

    draw = ImageDraw.Draw(img)
    img.paste(avatar, (50, 40), mask)

    # Fonts - Tailles correctes pour l'image
    try:
        font_name = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 90)
        font_small = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 70)
        font_xp = ImageFont.truetype("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf", 60)
    except:
        font_name = ImageFont.load_default()
        font_small = ImageFont.load_default()
        font_xp = ImageFont.load_default()

    # Texte utilisateur
    draw.text((380, 50), member.display_name[:25], font=font_name, fill=text_color)
    level_text = get_text(guild_id, "levels.card.level", default="Niveau")
    draw.text((380, 160), f"{level_text} {level}", font=font_small, fill=(200, 200, 200))

    # Barre d'XP
    bar_x, bar_y = 380, 300
    bar_w, bar_h = 1550, 130
    draw.rounded_rectangle((bar_x, bar_y, bar_x + bar_w, bar_y + bar_h), radius=18, fill=bar_bg)

    progress = max(0.0, min(progress, 1.0))
    fill_w = int(bar_w * progress)

    if fill_w > 0:
        # Gradient simplifié - 10 sections au lieu de pixel par pixel
        segments = min(10, fill_w)
        segment_width = max(1, fill_w // segments)
        for i in range(segments):
            ratio = i / max(1, segments - 1)
            r = int(bar_start[0] * (1 - ratio) + bar_end[0] * ratio)
            g = int(bar_start[1] * (1 - ratio) + bar_end[1] * ratio)
            b = int(bar_start[2] * (1 - ratio) + bar_end[2] * ratio)
            x_start = bar_x + i * segment_width
            x_end = min(bar_x + fill_w, x_start + segment_width)
            draw.rectangle((x_start, bar_y, x_end, bar_y + bar_h), fill=(r, g, b, 255))

    # Texte XP
    xp_text = f"{xp} / {xp_needed} XP"
    try:
        bbox = font_xp.getbbox(xp_text)
        tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    except:
        tw, th = 300, 60

    draw.text((bar_x + bar_w - tw - 30, bar_y + (bar_h - th)//2), xp_text, font=font_xp, fill=text_color)
    footer_text = get_text(guild_id, "levels.card.footer", default="Bravo pour ta montée de niveau!")
    draw.text((380, bar_y + bar_h + 20), footer_text, font=font_xp, fill=(180, 180, 180))

    buf = io.BytesIO()
    img.save(buf, format="PNG")
    buf.seek(0)
    return discord.File(buf, filename="niveau.png")

# ------------------------
# Cog Leveling
# ------------------------
class Leveling(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    # Gestion XP automatique
    @commands.Cog.listener()
    async def on_message(self, message):
        if message.author.bot or not message.guild:
            return

        guild_id = str(message.guild.id)
        user_id = str(message.author.id)
        data = load_data(guild_id)

        if user_id not in data:
            data[user_id] = {"xp": 0, "level": 0}

        xp_gain = random.randint(15, 20)
        data[user_id]["xp"] += xp_gain
        leveled_up, current_level, xp, xp_needed = process_xp_gain(data[user_id])
        save_data(guild_id, data)

        if leveled_up:
            embed = discord.Embed(
                title=get_text(int(guild_id), "levels.levelup.title", user=message.author.display_name),
                description=get_text(int(guild_id), "levels.levelup.description", level=current_level),
                color=0x00ff00,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Système de niveaux — Miaouscle Bot")
            await message.channel.send(content=f"{message.author.mention}", embed=embed)

    # Commande /niveau
    @commands.hybrid_command(name="niveau", description=get_text(0, "levels.commands.niveau_desc"))
    async def niveau(self, ctx, membre: discord.Member | None = None):
        try:
            if not ctx.guild:
                await ctx.send(get_text(0, "errors.command_guild_only"))
                return

            if membre is None:
                if isinstance(ctx.author, discord.Member):
                    membre = ctx.author
                else:
                    await ctx.send(get_text(0, "common.error_user_not_found"))
                    return
            
            # Empêcher de voir le niveau d'un bot
            if membre.bot:
                await ctx.send("❌ Impossible de consulter le niveau d'un bot.")
                return

            guild_id = str(ctx.guild.id)
            user_id = str(membre.id)
            data = load_data(guild_id)

            if user_id not in data:
                data[user_id] = {"xp": 0, "level": 0}
                save_data(guild_id, data)

            xp = data[user_id]["xp"]
            level = data[user_id]["level"]
            xp_needed = get_xp_needed(level)
            ratio = min(1.0, xp / xp_needed if xp_needed > 0 else 1.0)

            # Message temporaire
            embed = discord.Embed(
                title=get_text(int(guild_id), "levels.ui.generating"),
                description=get_text(int(guild_id), "levels.ui.waiting"),
                color=0x5865F2
            )
            loading = await ctx.send(embed=embed)

            # Lecture des options Premium (couleurs / image de fond) si le
            # serveur est éligible. Sinon on passe None et la carte reste
            # avec le style par défaut.
            premium_opts = None
            if has_premium(int(guild_id), 0, TIER_PREMIUM):
                cfg = await _load_server_config_async(int(guild_id))
                premium_opts = {
                    k: cfg.get(k) for k in (
                        "rank_bg_color",
                        "rank_bar_start_color",
                        "rank_bar_end_color",
                        "rank_text_color",
                        "rank_bg_image",
                    ) if cfg.get(k)
                } or None

            # Génération instantanée de l'image finale
            final_file = await generate_rank_image(
                membre, level, xp, xp_needed, ratio, int(guild_id), premium_opts
            )
            await loading.edit(content=None, embed=None, attachments=[final_file])
        except Exception as e:
            print(f"❌ Erreur /niveau: {e}")
            guild_id = ctx.guild.id if ctx.guild else 0
            await ctx.send(get_text(int(guild_id), "common.error_generating") + f" {str(e)[:100]}")

    # Commande /classement
    @commands.hybrid_command(name="classement", description=get_text(0, "levels.commands.classement_desc"))
    async def classement(self, ctx):
        try:
            if not ctx.guild:
                await ctx.send(get_text(0, "errors.command_guild_only"))
                return

            await ctx.defer()

            guild_id = str(ctx.guild.id)
            data = load_data(guild_id)

            if not data:
                await ctx.send(get_text(int(guild_id), "levels.errors.no_players"))
                return

            sorted_players = sorted(
                data.items(),
                key=lambda x: (x[1].get("level", 0), x[1].get("xp", 0)),
                reverse=True
            )

            embed = discord.Embed(
                title=f"🏆 Classement — {ctx.guild.name}",
                color=0xFFD700,
                timestamp=datetime.now(timezone.utc),
            )
            if ctx.guild.icon:
                embed.set_thumbnail(url=ctx.guild.icon.url)

            position = 1
            lines = []
            for user_id, user_data in sorted_players:
                member = ctx.guild.get_member(int(user_id))
                if member is None:
                    continue
                if member.bot:
                    continue

                level = user_data.get("level", 0)
                xp = user_data.get("xp", 0)
                xp_needed = get_xp_needed(level)
                ratio = min(1.0, xp / xp_needed) if xp_needed > 0 else 1.0
                bar_filled = int(ratio * 10)
                bar = "█" * bar_filled + "░" * (10 - bar_filled)

                medals = {1: "🥇", 2: "🥈", 3: "🥉"}
                medal = medals.get(position, f"**#{position}**")

                lines.append(
                    f"{medal} **{member.display_name}**\n"
                    f"┗ Niv. **{level}** · `{bar}` · {xp}/{xp_needed} XP"
                )
                position += 1

                if position > 10:
                    break

            if position == 1:
                await ctx.send(get_text(int(guild_id), "levels.errors.no_players"))
                return

            embed.description = "\n\n".join(lines)
            embed.set_footer(text=f"🎮 Top {position - 1} joueurs • Miaouscle Bot")
            await ctx.send(embed=embed)
        except Exception as e:
            print(f"❌ Erreur /classement: {e}")
            guild_id = ctx.guild.id if ctx.guild else 0
            try:
                await ctx.send(get_text(int(guild_id), "common.error_generating", default="❌ Erreur lors de la génération"))
            except Exception:
                pass

    # Commande /ajouter-xp
    @commands.hybrid_command(name="ajouter-xp", description=get_text(0, "levels.commands.ajouter_xp_desc"))
    @commands.has_permissions(manage_guild=True)
    async def ajouter_xp(self, ctx, membre: discord.Member, montant: int):
        # Empêcher d'ajouter de l'XP à un bot
        if membre.bot:
            await ctx.send("❌ Impossible d'ajouter de l'XP à un bot.")
            return
        
        if montant <= 0:
            await ctx.send(get_text(int(ctx.guild.id) if ctx.guild else 0, "levels.errors.invalid_xp_amount"))
            return

        guild_id = str(ctx.guild.id)
        user_id = str(membre.id)
        data = load_data(guild_id)
        if user_id not in data:
            data[user_id] = {"xp": 0, "level": 0}

        data[user_id]["xp"] += montant
        leveled_up, current_level, xp, xp_needed = process_xp_gain(data[user_id])
        save_data(guild_id, data)

        if leveled_up:
            embed = discord.Embed(
                title=get_text(int(guild_id), "levels.levelup.title", user=membre.display_name),
                description=get_text(int(guild_id), "levels.levelup.description", level=current_level),
                color=0x00ff00,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Système de niveaux — Miaouscle Bot")
            await ctx.send(content=f"{membre.mention}", embed=embed)
        else:
            embed = discord.Embed(
                title=get_text(int(guild_id), "levels.ui.xp_added"),
                description=get_text(int(guild_id), "levels.ui.xp_received", amount=montant, user=membre.mention),
                color=0x00ff00,
                timestamp=datetime.now(timezone.utc)
            )
            embed.set_footer(text="Système de niveaux — Miaouscle Bot")
            await ctx.send(embed=embed)

# ------------------------
# Setup
# ------------------------
async def setup(bot):
    await bot.add_cog(Leveling(bot))
