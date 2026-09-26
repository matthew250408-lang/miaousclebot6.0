"""Gestion des promotions tarifaires pour Miaouscle Bot."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Optional

PROMO_FILE = os.path.join(os.path.dirname(__file__), '..', 'web', 'data', 'promo.json')


def _load() -> dict:
    try:
        with open(PROMO_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"active": False, "items": [], "end_time": None, "label": ""}


def _save(data: dict) -> None:
    os.makedirs(os.path.dirname(PROMO_FILE), exist_ok=True)
    with open(PROMO_FILE, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def get_promo() -> Optional[dict]:
    """Retourne la promo active ou None si aucune promo en cours / expirée."""
    data = _load()
    if not data.get("active"):
        return None
    end_time = data.get("end_time")
    if end_time:
        try:
            end_dt = datetime.fromisoformat(str(end_time).replace("Z", "+00:00"))
            if end_dt.tzinfo is None:
                end_dt = end_dt.replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) >= end_dt.astimezone(timezone.utc):
                data["active"] = False
                _save(data)
                return None
        except (ValueError, TypeError):
            pass
    return data


def set_promo(label: str, end_time: datetime, items: list) -> None:
    """Active une nouvelle promo."""
    if end_time.tzinfo is None:
        end_time = end_time.replace(tzinfo=timezone.utc)
    end_time = end_time.astimezone(timezone.utc)
    _save({
        "active": True,
        "label": label,
        "end_time": end_time.isoformat().replace("+00:00", "Z"),
        "items": items,
    })


def clear_promo() -> None:
    """Désactive la promo en cours."""
    data = _load()
    data["active"] = False
    _save(data)


def get_promo_price(plan: str, billing: str) -> Optional[int]:
    """Retourne le prix promo en centimes pour (plan, billing) ou None."""
    promo = get_promo()
    if not promo:
        return None
    for item in promo.get("items", []):
        if item.get("plan") == plan and item.get("billing") == billing:
            return item["promo_cents"]
    return None
