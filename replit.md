# Bot 2026 — Miaouscle Bot

Bot Discord Miaouscle avec dashboard web Flask pour administrer les serveurs, les tickets, la modération, les niveaux et les fonctionnalités Premium.

## Run & Operate

- `uv run python web/app.py` depuis `bot/botdiscordoff` — lancer le dashboard (port 5000)
- `uv run python main.py` depuis `bot/botdiscordoff` — lancer le bot Discord
- Le bouton Run lance automatiquement les deux workflows.
- `uv sync --frozen` depuis `bot/botdiscordoff` — installer les dépendances Python
- Secret requis : `DISCORD_TOKEN`

## Stack

- Python 3.11+, `uv`
- Bot : `discord.py`
- Dashboard : Flask + Gunicorn
- Stockage : fichiers JSON locaux, avec support PostgreSQL pour certaines configurations

## Where things live

- `bot/botdiscordoff/main.py` — point d’entrée du bot Discord
- `bot/botdiscordoff/web/app.py` — dashboard Flask
- `bot/botdiscordoff/cogs/` — fonctionnalités Discord
- `bot/botdiscordoff/utils/` — services et stockage partagés
- `bot/botdiscordoff/web/templates/` — pages du dashboard

## Architecture decisions

- Le bot et le dashboard sont deux processus distincts lancés en parallèle.
- Le dashboard utilise le port 5000 et le bot ne publie pas de port web.
- Les données locales du dashboard restent dans `bot/botdiscordoff/web/data/`.

## Product

Miaouscle Bot fournit des outils de gestion Discord : modération, anti-raid, tickets, niveaux XP, rôles Premium, statistiques, maintenance et administration depuis un dashboard web.

## User preferences

- Réponses et interface en français lorsque c'est pertinent.

## Gotchas

- Le secret `DISCORD_TOKEN` doit être enregistré dans les Secrets Replit ; ne jamais le mettre dans un fichier ou dans le chat.
- Les workflows générés pour les anciens artifacts ne sont pas nécessaires pour lancer Bot 2026 ; utiliser `Project`.

## Pointers
- Voir `bot/botdiscordoff/README.md` et `bot/botdiscordoff/CHANGELOG.md` pour le contexte du projet.
