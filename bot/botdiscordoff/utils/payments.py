"""Module de paiements pour Miaouscle Bot.

Ce module est volontairement **défensif** : tout est conçu pour qu'AUCUN
appel réel à Stripe ou PayPal ne puisse être déclenché tant que les clés
ne sont pas configurées dans les variables d'environnement.

Tant qu'on est en phase « Bientôt disponible », `is_payments_enabled()`
renvoie False et toutes les routes de checkout doivent renvoyer 503.

Variables d'environnement attendues (à fournir uniquement quand on passe
en mode actif) :
    STRIPE_SECRET_KEY        ex: sk_test_... ou sk_live_...
    STRIPE_PUBLISHABLE_KEY   ex: pk_test_... ou pk_live_...
    STRIPE_WEBHOOK_SECRET    ex: whsec_...
    PAYPAL_CLIENT_ID         ID OAuth de l'app PayPal
    PAYPAL_CLIENT_SECRET     Secret OAuth de l'app PayPal
    PAYPAL_WEBHOOK_ID        ID du webhook PayPal (pour vérif signature) - optionnel
    PAYPAL_MODE              "sandbox" (défaut) ou "live"

Le statut **Premium** dans le dashboard reste piloté par
`web/data/premium.json` ; ce module ajoute juste la traçabilité des
commandes (`web/data/orders.json`) et la liaison avec un éventuel
abonnement récurrent (`web/data/subscriptions.json`).
"""
from __future__ import annotations

import json
import os
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import requests

# ---------------------------------------------------------------------------
# Constantes de catalogue (source de vérité côté serveur)
# Les montants sont en CENTIMES d'euro, devise = EUR.
# Ils doivent rester strictement identiques à ce qu'affiche premium.html
# pour qu'un client ne puisse JAMAIS payer un montant manipulé côté front.
# ---------------------------------------------------------------------------

CURRENCY = "eur"

PLAN_PREMIUM = "premium"
PLAN_PREMIUM_PLUS = "premium_plus"

BILLING_MONTHLY = "monthly"
BILLING_YEARLY = "yearly"

# (plan, billing) -> (montant_centimes, label_humain)
CATALOG: Dict[Tuple[str, str], Tuple[int, str]] = {
    (PLAN_PREMIUM, BILLING_MONTHLY): (299, "Premium - mensuel"),
    (PLAN_PREMIUM, BILLING_YEARLY): (2999, "Premium - annuel"),
    (PLAN_PREMIUM_PLUS, BILLING_MONTHLY): (599, "Premium+ - mensuel"),
    (PLAN_PREMIUM_PLUS, BILLING_YEARLY): (5999, "Premium+ - annuel"),
}


def get_catalog_entry(plan: str, billing: str) -> Optional[Tuple[int, str]]:
    """Renvoie (montant_cents, label) pour un couple (plan, billing) ou None.

    On valide STRICTEMENT côté serveur — jamais d'amount fourni par le client.
    """
    return CATALOG.get((plan, billing))


# ---------------------------------------------------------------------------
# Détection de configuration
# ---------------------------------------------------------------------------


def is_stripe_configured() -> bool:
    return bool(os.environ.get("STRIPE_SECRET_KEY"))


def is_paypal_configured() -> bool:
    return bool(
        os.environ.get("PAYPAL_CLIENT_ID")
        and os.environ.get("PAYPAL_CLIENT_SECRET")
    )


def is_provider_enabled(provider: str) -> bool:
    """Indique si un fournisseur est configuré et autorisé par l'admin."""
    configured = {
        "stripe": is_stripe_configured,
        "paypal": is_paypal_configured,
    }.get(provider)
    if configured is None:
        return False
    settings = get_payment_settings()
    return bool(configured() and settings["providers"].get(provider, False))


def is_payments_enabled() -> bool:
    """True si au moins un fournisseur configuré est autorisé par l'admin."""
    return is_provider_enabled("stripe") or is_provider_enabled("paypal")


def get_payment_status() -> Dict[str, Any]:
    """Statut public (sûr à exposer côté front) des fournisseurs disponibles."""
    settings = get_payment_settings()
    provider_configured = {
        "stripe": is_stripe_configured(),
        "paypal": is_paypal_configured(),
    }
    provider_enabled = {
        provider: bool(provider_configured[provider] and settings["providers"].get(provider, False))
        for provider in provider_configured
    }
    return {
        "enabled": any(provider_enabled.values()),
        "providers": provider_enabled,
        "configured": provider_configured,
        "settings": settings,
        "plans": settings["plans"],
        "currency": CURRENCY,
        "mode_paypal": os.environ.get("PAYPAL_MODE", "sandbox"),
        # Le frontend pourra utiliser ça pour Stripe.js
        "stripe_publishable_key": os.environ.get("STRIPE_PUBLISHABLE_KEY", ""),
    }


# ---------------------------------------------------------------------------
# Stockage JSON — orders & subscriptions
# ---------------------------------------------------------------------------

_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_DATA_DIR = os.path.join(_PROJECT_ROOT, "web", "data")
_ORDERS_FILE = os.path.join(_DATA_DIR, "orders.json")
_SUBS_FILE = os.path.join(_DATA_DIR, "subscriptions.json")
_PAYMENT_SETTINGS_FILE = os.path.join(_DATA_DIR, "payment_settings.json")


def _load_json(path: str, default: Any) -> Any:
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
    except Exception as e:
        print(f"[payments] Lecture {path} échouée : {e}")
    return default


def _save_json_atomic(path: str, data: Any) -> bool:
    """Écriture atomique : on écrit dans .tmp puis on renomme.

    Évite la corruption du fichier si le process meurt en plein milieu.
    """
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        os.replace(tmp, path)
        return True
    except Exception as e:
        print(f"[payments] Écriture {path} échouée : {e}")
        return False


def get_payment_settings() -> Dict[str, Any]:
    """Charge les interrupteurs admin avec des valeurs sûres par défaut."""
    raw = _load_json(_PAYMENT_SETTINGS_FILE, {})
    raw = raw if isinstance(raw, dict) else {}
    raw_providers = raw.get("providers", {})
    raw_plans = raw.get("plans", {})
    raw_providers = raw_providers if isinstance(raw_providers, dict) else {}
    raw_plans = raw_plans if isinstance(raw_plans, dict) else {}
    return {
        "providers": {
            "stripe": bool(raw_providers.get("stripe", True)),
            "paypal": bool(raw_providers.get("paypal", True)),
        },
        "plans": {
            PLAN_PREMIUM: bool(raw_plans.get(PLAN_PREMIUM, True)),
            PLAN_PREMIUM_PLUS: bool(raw_plans.get(PLAN_PREMIUM_PLUS, True)),
        },
        "updated_at": raw.get("updated_at"),
    }


def save_payment_settings(providers: Dict[str, Any], plans: Dict[str, Any]) -> Dict[str, Any]:
    """Enregistre uniquement les fournisseurs et plans connus."""
    settings = {
        "providers": {
            "stripe": bool(providers.get("stripe", False)),
            "paypal": bool(providers.get("paypal", False)),
        },
        "plans": {
            PLAN_PREMIUM: bool(plans.get(PLAN_PREMIUM, False)),
            PLAN_PREMIUM_PLUS: bool(plans.get(PLAN_PREMIUM_PLUS, False)),
        },
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    if not _save_json_atomic(_PAYMENT_SETTINGS_FILE, settings):
        raise OSError("Impossible d'enregistrer les réglages de paiement")
    return settings


def is_plan_enabled(plan: str) -> bool:
    """Indique si un plan est autorisé à la souscription."""
    return bool(get_payment_settings()["plans"].get(plan, False))


def load_orders() -> List[Dict[str, Any]]:
    data = _load_json(_ORDERS_FILE, [])
    return data if isinstance(data, list) else []


def save_orders(orders: List[Dict[str, Any]]) -> bool:
    return _save_json_atomic(_ORDERS_FILE, orders)


def load_subscriptions() -> Dict[str, Any]:
    data = _load_json(_SUBS_FILE, {})
    return data if isinstance(data, dict) else {}


def save_subscriptions(subs: Dict[str, Any]) -> bool:
    return _save_json_atomic(_SUBS_FILE, subs)


def create_pending_order(
    *,
    user_id: str,
    guild_id: str,
    plan: str,
    billing: str,
    provider: str,
    amount_cents: int,
    currency: str = CURRENCY,
) -> Dict[str, Any]:
    """Crée une entrée d'ordre en statut `pending` et la persiste.

    Renvoie le dict de l'ordre créé (avec un `id` interne unique).
    """
    order = {
        "id": uuid.uuid4().hex,
        "created_at": int(time.time()),
        "updated_at": int(time.time()),
        "user_id": str(user_id),
        "guild_id": str(guild_id),
        "plan": plan,
        "billing": billing,
        "provider": provider,
        "amount_cents": int(amount_cents),
        "currency": currency,
        "status": "pending",
        "provider_session_id": None,
        "provider_payment_id": None,
        "metadata": {},
    }
    orders = load_orders()
    orders.append(order)
    save_orders(orders)
    return order


def update_order(order_id: str, patch: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    """Met à jour un ordre existant. Renvoie le nouvel état ou None."""
    orders = load_orders()
    found = None
    for o in orders:
        if o.get("id") == order_id:
            o.update(patch)
            o["updated_at"] = int(time.time())
            found = o
            break
    if found is not None:
        save_orders(orders)
    return found


def find_order_by_provider_session(
    provider: str, session_id: str
) -> Optional[Dict[str, Any]]:
    if not session_id:
        return None
    for o in load_orders():
        if o.get("provider") == provider and o.get("provider_session_id") == session_id:
            return o
    return None


def get_user_orders(user_id: str) -> List[Dict[str, Any]]:
    return [o for o in load_orders() if o.get("user_id") == str(user_id)]


# ---------------------------------------------------------------------------
# Garde-fous publics
# ---------------------------------------------------------------------------

PAYMENTS_DISABLED_MESSAGE = (
    "Les paiements en ligne ne sont pas encore activés. Le système est en "
    "préparation, l'ouverture officielle sera annoncée sur le Discord."
)


def assert_payments_enabled() -> Tuple[bool, str]:
    """Garde-fou central : à appeler AVANT toute tentative de checkout.

    Renvoie (ok, message). Tant que `enabled=False`, renvoyer 503 côté API.
    """
    if not is_payments_enabled():
        return False, PAYMENTS_DISABLED_MESSAGE
    return True, ""


# ---------------------------------------------------------------------------
# PayPal — helpers (OAuth, ensure plan, create subscription, verify webhook)
# ---------------------------------------------------------------------------

def _paypal_base_url() -> str:
    """Renvoie l'URL de base de l'API PayPal selon le mode."""
    mode = os.environ.get("PAYPAL_MODE", "sandbox").strip().lower()
    if mode == "live":
        return "https://api-m.paypal.com"
    return "https://api-m.sandbox.paypal.com"


_paypal_token_cache: Dict[str, Any] = {"access_token": None, "expires_at": 0}


def paypal_get_access_token() -> Optional[str]:
    """Récupère un access token OAuth PayPal (client_credentials).

    Met en cache jusqu'à 60s avant l'expiration. Renvoie None si non configuré
    ou si l'appel échoue (auquel cas l'appelant doit traiter ça comme 503).
    """
    if not is_paypal_configured():
        return None

    now = time.time()
    if _paypal_token_cache["access_token"] and now < _paypal_token_cache["expires_at"]:
        return _paypal_token_cache["access_token"]

    try:
        r = requests.post(
            f"{_paypal_base_url()}/v1/oauth2/token",
            auth=(os.environ["PAYPAL_CLIENT_ID"], os.environ["PAYPAL_CLIENT_SECRET"]),
            data={"grant_type": "client_credentials"},
            headers={"Accept": "application/json"},
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[paypal] OAuth échoué HTTP {r.status_code}: {r.text[:200]}")
            return None
        data = r.json()
        token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        if not token:
            return None
        _paypal_token_cache["access_token"] = token
        _paypal_token_cache["expires_at"] = now + max(60, expires_in - 60)
        return token
    except Exception as e:
        print(f"[paypal] OAuth exception: {e}")
        return None


def _paypal_plan_cache_key(plan: str, billing: str) -> str:
    """Clé de cache pour stocker un plan_id PayPal dans subscriptions.json."""
    return f"paypal_plan:{plan}:{billing}"


def paypal_ensure_plan(plan: str, billing: str) -> Optional[str]:
    """Trouve ou crée un plan PayPal pour (plan, billing). Renvoie le plan_id.

    Idempotent : on cache le couple (product_id, plan_id) dans subscriptions.json
    pour ne pas recréer à chaque checkout. La création se fait en deux étapes :
      1. POST /v1/catalogs/products → product_id
      2. POST /v1/billing/plans → plan_id (lié au product, prix figé serveur)
    """
    entry = get_catalog_entry(plan, billing)
    if entry is None:
        return None
    amount_cents, label = entry

    cache_key = _paypal_plan_cache_key(plan, billing)
    subs = load_subscriptions()
    cached = subs.get(cache_key)
    if isinstance(cached, dict) and cached.get("plan_id"):
        return cached["plan_id"]

    token = paypal_get_access_token()
    if not token:
        return None

    base = _paypal_base_url()
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    # 1) Product (catalog)
    try:
        product_resp = requests.post(
            f"{base}/v1/catalogs/products",
            json={
                "name": f"Miaouscle {label}",
                "description": f"Abonnement Miaouscle Bot — {label}",
                "type": "SERVICE",
                "category": "SOFTWARE",
            },
            headers={**headers, "PayPal-Request-Id": f"prod-{plan}-{billing}-{uuid.uuid4().hex[:8]}"},
            timeout=15,
        )
        if product_resp.status_code not in (200, 201):
            print(f"[paypal] product create HTTP {product_resp.status_code}: {product_resp.text[:200]}")
            return None
        product_id = product_resp.json().get("id")
        if not product_id:
            return None
    except Exception as e:
        print(f"[paypal] product create exception: {e}")
        return None

    # 2) Plan (billing)
    interval_unit = "MONTH" if billing == BILLING_MONTHLY else "YEAR"
    amount_str = f"{amount_cents / 100:.2f}"
    try:
        plan_resp = requests.post(
            f"{base}/v1/billing/plans",
            json={
                "product_id": product_id,
                "name": f"Miaouscle {label}",
                "description": f"Abonnement {label}",
                "status": "ACTIVE",
                "billing_cycles": [{
                    "frequency": {"interval_unit": interval_unit, "interval_count": 1},
                    "tenure_type": "REGULAR",
                    "sequence": 1,
                    "total_cycles": 0,  # 0 = infini
                    "pricing_scheme": {
                        "fixed_price": {"value": amount_str, "currency_code": "EUR"}
                    },
                }],
                "payment_preferences": {
                    "auto_bill_outstanding": True,
                    "setup_fee_failure_action": "CONTINUE",
                    "payment_failure_threshold": 2,
                },
            },
            headers={**headers, "PayPal-Request-Id": f"plan-{plan}-{billing}-{uuid.uuid4().hex[:8]}"},
            timeout=15,
        )
        if plan_resp.status_code not in (200, 201):
            print(f"[paypal] plan create HTTP {plan_resp.status_code}: {plan_resp.text[:300]}")
            return None
        plan_id = plan_resp.json().get("id")
        if not plan_id:
            return None
    except Exception as e:
        print(f"[paypal] plan create exception: {e}")
        return None

    # Cache pour ne plus jamais recréer ce couple
    subs = load_subscriptions()
    subs[cache_key] = {
        "product_id": product_id,
        "plan_id": plan_id,
        "amount_cents": amount_cents,
        "currency": "EUR",
        "created_at": int(time.time()),
    }
    save_subscriptions(subs)
    return plan_id


def paypal_create_subscription(
    *, plan_id: str, return_url: str, cancel_url: str, custom_id: str
) -> Optional[Dict[str, Any]]:
    """Crée une souscription PayPal et renvoie {id, approval_url} ou None.

    `custom_id` doit contenir notre order_id interne pour que le webhook
    puisse retrouver la commande au moment de l'activation.
    """
    token = paypal_get_access_token()
    if not token:
        return None
    try:
        r = requests.post(
            f"{_paypal_base_url()}/v1/billing/subscriptions",
            json={
                "plan_id": plan_id,
                "custom_id": custom_id,
                "application_context": {
                    "brand_name": "Miaouscle Bot",
                    "locale": "fr-FR",
                    "shipping_preference": "NO_SHIPPING",
                    "user_action": "SUBSCRIBE_NOW",
                    "return_url": return_url,
                    "cancel_url": cancel_url,
                },
            },
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Accept": "application/json",
                "PayPal-Request-Id": f"sub-{custom_id}-{uuid.uuid4().hex[:6]}",
            },
            timeout=15,
        )
        if r.status_code not in (200, 201):
            print(f"[paypal] subscription create HTTP {r.status_code}: {r.text[:300]}")
            return None
        data = r.json()
        approval_url = None
        for link in data.get("links", []) or []:
            if link.get("rel") == "approve":
                approval_url = link.get("href")
                break
        return {"id": data.get("id"), "approval_url": approval_url}
    except Exception as e:
        print(f"[paypal] subscription create exception: {e}")
        return None


def paypal_verify_webhook(headers: Dict[str, str], body_str: str) -> bool:
    """Vérifie la signature d'un webhook PayPal via leur API officielle.

    Si PAYPAL_WEBHOOK_ID n'est pas posé en env, renvoie False (refus par défaut).
    L'appelant peut décider de logger un avertissement et de NE PAS appliquer
    d'effet de bord tant que le webhook n'est pas correctement configuré.
    """
    webhook_id = os.environ.get("PAYPAL_WEBHOOK_ID", "").strip()
    if not webhook_id:
        return False
    token = paypal_get_access_token()
    if not token:
        return False
    try:
        body_obj = json.loads(body_str) if body_str else {}
    except Exception:
        return False

    payload = {
        "auth_algo": headers.get("PAYPAL-AUTH-ALGO") or headers.get("Paypal-Auth-Algo", ""),
        "cert_url": headers.get("PAYPAL-CERT-URL") or headers.get("Paypal-Cert-Url", ""),
        "transmission_id": headers.get("PAYPAL-TRANSMISSION-ID") or headers.get("Paypal-Transmission-Id", ""),
        "transmission_sig": headers.get("PAYPAL-TRANSMISSION-SIG") or headers.get("Paypal-Transmission-Sig", ""),
        "transmission_time": headers.get("PAYPAL-TRANSMISSION-TIME") or headers.get("Paypal-Transmission-Time", ""),
        "webhook_id": webhook_id,
        "webhook_event": body_obj,
    }

    try:
        r = requests.post(
            f"{_paypal_base_url()}/v1/notifications/verify-webhook-signature",
            json=payload,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            timeout=10,
        )
        if r.status_code != 200:
            print(f"[paypal] verify webhook HTTP {r.status_code}: {r.text[:200]}")
            return False
        return r.json().get("verification_status") == "SUCCESS"
    except Exception as e:
        print(f"[paypal] verify webhook exception: {e}")
        return False


# ---------------------------------------------------------------------------
# Annulation d'abonnement (depuis le dashboard)
# ---------------------------------------------------------------------------


def find_active_order_for_guild(guild_id: str) -> Optional[Dict[str, Any]]:
    """Trouve l'ordre actif (status=paid) le plus récent pour un serveur."""
    guild_id = str(guild_id)
    paid = [
        o for o in load_orders()
        if o.get("guild_id") == guild_id
        and o.get("status") == "paid"
        and o.get("provider_payment_id")
    ]
    if not paid:
        return None
    return sorted(paid, key=lambda o: o.get("updated_at", 0), reverse=True)[0]


def cancel_stripe_subscription(sub_id: str) -> Tuple[bool, str]:
    """Annule un abonnement Stripe. Renvoie (ok, message)."""
    if not is_stripe_configured():
        return False, "Stripe non configuré"
    try:
        import stripe  # type: ignore
        stripe.api_key = os.environ["STRIPE_SECRET_KEY"]
        stripe.Subscription.cancel(sub_id)
        return True, "Abonnement annulé avec succès"
    except Exception as e:
        print(f"[stripe] cancel subscription error: {e}")
        return False, str(e)


def cancel_paypal_subscription(sub_id: str) -> Tuple[bool, str]:
    """Annule un abonnement PayPal. Renvoie (ok, message)."""
    if not is_paypal_configured():
        return False, "PayPal non configuré"
    try:
        token = paypal_get_access_token()
        if not token:
            return False, "Impossible d'obtenir un token PayPal"
        r = requests.post(
            f"{_paypal_base_url()}/v1/billing/subscriptions/{sub_id}/cancel",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={"reason": "Cancelled by user via dashboard"},
            timeout=15,
        )
        if r.status_code in (200, 204):
            return True, "Abonnement annulé avec succès"
        return False, f"Erreur PayPal {r.status_code}: {r.text[:200]}"
    except Exception as e:
        print(f"[paypal] cancel subscription error: {e}")
        return False, str(e)


# ---------------------------------------------------------------------------
# Premium+ multi-server slots
# ---------------------------------------------------------------------------

MAX_PLUS_SLOTS = 3

_SLOTS_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "..", "web", "data", "premium_plus_slots.json",
)


def load_slots() -> Dict[str, Any]:
    """Charge le fichier de slots Premium+."""
    try:
        path = os.path.normpath(_SLOTS_FILE)
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def save_slots(data: Dict[str, Any]) -> None:
    """Sauvegarde le fichier de slots Premium+."""
    path = os.path.normpath(_SLOTS_FILE)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def init_plus_slots(order_id: str, user_id: str, initial_guild_id: str) -> None:
    """Crée l'entrée de slots pour une commande Premium+ nouvellement payée."""
    slots = load_slots()
    if order_id not in slots:
        slots[order_id] = {
            "user_id": str(user_id),
            "max_slots": MAX_PLUS_SLOTS,
            "guild_ids": [str(initial_guild_id)],
        }
        save_slots(slots)
        print(f"[slots] ✅ Slots Premium+ initialisés pour ordre {order_id}, serveur {initial_guild_id}")


def get_slots_for_order(order_id: str) -> Optional[Dict[str, Any]]:
    """Renvoie les données de slots d'un ordre ou None si inexistant."""
    return load_slots().get(order_id)


def get_plus_order_for_user(user_id: str) -> Optional[Dict[str, Any]]:
    """Retourne l'ordre Premium+ actif (paid) d'un user, ou None."""
    user_id = str(user_id)
    orders = load_orders()
    paid_plus = [
        o for o in orders
        if o.get("plan") == PLAN_PREMIUM_PLUS
        and o.get("status") == "paid"
        and str(o.get("user_id", "")) == user_id
    ]
    if not paid_plus:
        return None
    return sorted(paid_plus, key=lambda o: o.get("updated_at", 0), reverse=True)[0]


def add_guild_to_plus_slots(order_id: str, guild_id: str) -> Tuple[bool, str]:
    """Ajoute un serveur aux slots Premium+ d'un ordre. Renvoie (ok, message)."""
    guild_id = str(guild_id)
    slots = load_slots()
    entry = slots.get(order_id)
    if not entry:
        return False, "Abonnement Premium+ introuvable"
    guild_ids = entry.get("guild_ids", [])
    if guild_id in guild_ids:
        return False, "Ce serveur est déjà activé"
    if len(guild_ids) >= entry.get("max_slots", MAX_PLUS_SLOTS):
        return False, f"Limite de {entry.get('max_slots', MAX_PLUS_SLOTS)} serveurs atteinte"
    guild_ids.append(guild_id)
    entry["guild_ids"] = guild_ids
    slots[order_id] = entry
    save_slots(slots)
    return True, "Serveur ajouté"


def remove_guild_from_plus_slots(order_id: str, guild_id: str) -> Tuple[bool, str]:
    """Retire un serveur des slots Premium+ d'un ordre. Renvoie (ok, message)."""
    guild_id = str(guild_id)
    slots = load_slots()
    entry = slots.get(order_id)
    if not entry:
        return False, "Abonnement Premium+ introuvable"
    guild_ids = entry.get("guild_ids", [])
    if guild_id not in guild_ids:
        return False, "Ce serveur n'est pas dans les slots"
    guild_ids.remove(guild_id)
    entry["guild_ids"] = guild_ids
    slots[order_id] = entry
    save_slots(slots)
    return True, "Serveur retiré"


def get_all_plus_guild_ids(order_id: str) -> List[str]:
    """Renvoie la liste de tous les guild_ids d'un ordre Premium+."""
    entry = get_slots_for_order(order_id)
    if not entry:
        return []
    return list(entry.get("guild_ids", []))
