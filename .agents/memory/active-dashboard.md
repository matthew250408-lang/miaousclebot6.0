---
name: Active dashboard source
description: Which duplicated project copy serves the live Flask dashboard and Discord bot.
---

The active API workflow runs from `bot/botdiscordoff` via its `start.sh`; the Flask templates and Discord cogs in that tree are the source of truth for the user-facing dashboard and bot behavior. Other `botbot/` trees can be stale duplicates and may have different workflow dependencies.

**Why:** The workspace contains several duplicated artifact trees, and editing the wrong copy can appear successful while changing nothing in the running service.

**How to apply:** Before changing this product, confirm the managed workflow's working directory and edit the matching `bot/botdiscordoff` files. Restart `artifacts/api-server: Bot Web Dashboard` after bot or template changes.