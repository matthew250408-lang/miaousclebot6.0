# Miaouscle Bot

Bot Discord multilingue (FR/EN/ES/DE) avec dashboard web, modération, niveaux XP, tickets, et système premium.

## Run & Operate

- **Discord Bot** — workflow `Discord Bot` → `python main.py`
- **Web Dashboard** — workflow `Web Dashboard` → `cd web && python app.py` (port 5000)
- **Arrêter/Redémarrer** — via l'onglet Workflows dans Replit

## Stack

- Python 3.11, discord.py 2.x, Flask 3.x
- PostgreSQL (Replit DB) via psycopg3 — `DATABASE_URL` auto-configuré
- OAuth2 Discord pour l'authentification du dashboard

## Where things live

- `main.py` — Entry point du bot Discord
- `cogs/` — Modules de commandes (admin, modération, niveaux, tickets, premium…)
- `utils/` — Utilitaires (database, premium, logger, translations, payments…)
- `web/app.py` — Dashboard web Flask (OAuth2 Discord)
- `web/templates/` — Pages HTML du dashboard
- `translations/` — Fichiers de traduction (fr/en/es/de)
- `data/` — Données JSON (XP, avertissements…)
- `config.json` — Configs serveurs (miroir legacy de PostgreSQL)
- `version.json` / `version.txt` — Version courante du bot

## Architecture decisions

- Les configs serveur sont stockées en PostgreSQL (table `server_configs`) avec fallback JSON local
- Le verrou singleton (`utils/singleton_lock.py`) empêche 2 instances du bot de tourner simultanément via `pg_try_advisory_lock`
- Les rôles Premium sont synchronisés automatiquement toutes les 5 min + au join + après chaque grant/revoke
- La maintenance BOT et SITE sont indépendantes — le bot peut être en maintenance sans bloquer le site

## Secrets requis

- `DISCORD_TOKEN` — Token du bot (Portail développeur Discord > Bot)
- `DISCORD_CLIENT_SECRET` — Secret OAuth2 (Portail développeur Discord > OAuth2)
- `DATABASE_URL` — Auto-configuré par Replit (PostgreSQL)
- `OWNER_PASSWORD` — Mot de passe admin dashboard (défaut: @user-1234@)
- `DISCORD_CLIENT_ID` — ID de l'application Discord (défaut: 1157699815833088001)

## OAuth2 — URL de redirection à configurer

Dans le portail Discord Developer (https://discord.com/developers/applications/1157699815833088001/oauth2) :
- Ajouter l'URL de callback : `https://<ton-domaine-replit>/callback`

## User preferences

- Langue : français
- Version actuelle : v13.8.10

## Gotchas

- Toujours relancer le workflow `Discord Bot` après modification de `main.py` ou d'un cog
- Le workflow `Web Dashboard` doit tourner en parallèle du bot pour que le dashboard soit accessible
- Ne pas modifier `DATABASE_URL` — géré automatiquement par Replit
