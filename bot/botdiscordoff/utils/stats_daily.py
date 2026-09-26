"""
utils/stats_daily.py — Stockage léger des statistiques journalières par serveur.

Format : un fichier JSONL par serveur dans `web/data/stats_daily/{gid}.jsonl`.
Chaque ligne est un dict { "date": "YYYY-MM-DD", "members": int, "messages": int,
"sanctions": int }.

Conçu pour être lu par le dashboard et alimenté par un cog du bot
(stats_collector). Pas de dépendance externe, append-only, tolérant aux
fichiers manquants.
"""
from __future__ import annotations

import json
import os
import threading
from datetime import date, datetime, timedelta, timezone
from typing import Iterable, Optional

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_STATS_DIR = os.path.join(_PROJECT_ROOT, "web", "data", "stats_daily")
_LOCK = threading.Lock()


def _path_for(gid: int | str) -> str:
    os.makedirs(_STATS_DIR, exist_ok=True)
    return os.path.join(_STATS_DIR, f"{gid}.jsonl")


def read_history(gid: int | str, days: int = 30) -> list[dict]:
    """Retourne la liste des entrées des `days` derniers jours, anciennes → récentes."""
    path = _path_for(gid)
    if not os.path.exists(path):
        return []
    cutoff = (datetime.now(timezone.utc).date() - timedelta(days=days - 1)).isoformat()
    out: list[dict] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(entry, dict):
                    continue
                d = entry.get("date")
                if not isinstance(d, str):
                    continue
                if d >= cutoff:
                    out.append(entry)
    except OSError:
        return []
    out.sort(key=lambda e: e.get("date", ""))
    return out


def upsert_today(
    gid: int | str,
    *,
    members: Optional[int] = None,
    messages: Optional[int] = None,
    sanctions: Optional[int] = None,
) -> None:
    """Écrit/maj l'entrée du jour pour un serveur (idempotent par jour).

    Lit le fichier complet, met à jour ou ajoute la ligne du jour, réécrit
    le fichier. Coût : un fichier rare (≤ ~3650 lignes pour 10 ans), suffisant.
    """
    today = datetime.now(timezone.utc).date().isoformat()
    path = _path_for(gid)
    with _LOCK:
        entries: list[dict] = []
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            e = json.loads(line)
                            if isinstance(e, dict) and isinstance(e.get("date"), str):
                                entries.append(e)
                        except json.JSONDecodeError:
                            continue
            except OSError:
                pass
        # Trouve / crée l'entrée du jour
        target = None
        for e in entries:
            if e.get("date") == today:
                target = e
                break
        if target is None:
            target = {"date": today, "members": 0, "messages": 0, "sanctions": 0}
            entries.append(target)
        if members is not None:
            target["members"] = int(members)
        if messages is not None:
            target["messages"] = int(messages)
        if sanctions is not None:
            target["sanctions"] = int(sanctions)
        # Réécrit le fichier
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e, ensure_ascii=False) + "\n")
        os.replace(tmp, path)


def increment_messages(gid: int | str, by: int = 1) -> None:
    """Incrémente le compteur messages du jour."""
    today = datetime.now(timezone.utc).date().isoformat()
    history = read_history(gid, days=1)
    current = next((e for e in history if e.get("date") == today), None)
    new_val = (current.get("messages", 0) if current else 0) + by
    upsert_today(gid, messages=new_val)


def increment_sanctions(gid: int | str, by: int = 1) -> None:
    """Incrémente le compteur sanctions du jour."""
    today = datetime.now(timezone.utc).date().isoformat()
    history = read_history(gid, days=1)
    current = next((e for e in history if e.get("date") == today), None)
    new_val = (current.get("sanctions", 0) if current else 0) + by
    upsert_today(gid, sanctions=new_val)
