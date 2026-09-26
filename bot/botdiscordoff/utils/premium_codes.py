"""
Système de codes Premium Miaouscle Bot.

Permet de générer des codes Premium / Premium+ que l'on peut offrir à un
utilisateur précis (ex : récompense, concours, futur paiement Stripe). Le
destinataire peut consulter ses codes via la slash `/code premium` puis les
appliquer à un de ses serveurs (via le dashboard ou plus tard via Discord).

Stockage : table PostgreSQL `premium_codes` (idempotent, créée au boot).
Fallback : aucune persistance JSON — si DATABASE_URL absent, le module est
inactif (les commandes répondent un message clair).
"""
from __future__ import annotations

import logging
import os
import secrets
import threading
from datetime import datetime, timezone, timedelta
from typing import Optional

from utils.premium import (
    TIER_PREMIUM, TIER_PREMIUM_PLUS,
    grant_premium,
)

logger = logging.getLogger(__name__)

_SCHEMA_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False

CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_PREFIX = "MIA"
CODE_GROUPS = 3
CODE_GROUP_LEN = 4


def _get_dsn() -> Optional[str]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return dsn


def _connect():
    import psycopg
    dsn = _get_dsn()
    if not dsn:
        raise RuntimeError("DATABASE_URL non configuré")
    return psycopg.connect(dsn, connect_timeout=10)


def init_schema() -> bool:
    """Crée la table `premium_codes` si nécessaire. Idempotent et thread-safe."""
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return True
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_INITIALIZED:
            return True
        if not _get_dsn():
            logger.warning("DATABASE_URL absent — premium_codes désactivé.")
            return False
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS premium_codes (
                            code                  VARCHAR(32) PRIMARY KEY,
                            tier                  VARCHAR(16) NOT NULL,
                            duration_days         INTEGER,
                            recipient_user_id     BIGINT,
                            created_by            BIGINT NOT NULL,
                            created_at            TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                            expires_at            TIMESTAMPTZ,
                            redeemed_at           TIMESTAMPTZ,
                            redeemed_by_user_id   BIGINT,
                            redeemed_by_guild_id  BIGINT,
                            note                  TEXT
                        )
                        """
                    )
                    cur.execute(
                        "CREATE INDEX IF NOT EXISTS premium_codes_recipient_idx "
                        "ON premium_codes (recipient_user_id) WHERE redeemed_at IS NULL"
                    )
                conn.commit()
            _SCHEMA_INITIALIZED = True
            logger.info("✅ premium_codes: schéma PostgreSQL prêt.")
            return True
        except Exception as e:
            logger.error(f"❌ premium_codes init schéma : {e}", exc_info=True)
            return False


def _make_code() -> str:
    """Génère un code lisible : MIA-XXXX-YYYY-ZZZZ (alphabet sans confusion)."""
    groups = [
        "".join(secrets.choice(CODE_ALPHABET) for _ in range(CODE_GROUP_LEN))
        for _ in range(CODE_GROUPS)
    ]
    return f"{CODE_PREFIX}-" + "-".join(groups)


def generate_code(
    *,
    tier: str,
    duration_days: Optional[int],
    created_by: int,
    recipient_user_id: Optional[int] = None,
    expires_in_days: Optional[int] = 90,
    note: Optional[str] = None,
) -> Optional[str]:
    """Crée un code Premium. Retourne le code généré, ou None si la DB est KO.

    - tier : 'premium' ou 'premium_plus'
    - duration_days : durée du Premium accordé une fois le code utilisé
                      (None = à vie / lifetime)
    - recipient_user_id : si défini, seul cet utilisateur verra ce code via
                          `/code premium`
    - expires_in_days : combien de jours le code reste valable avant
                        expiration sans utilisation (None = pas d'expiration)
    """
    if tier not in (TIER_PREMIUM, TIER_PREMIUM_PLUS):
        raise ValueError("tier doit être 'premium' ou 'premium_plus'")
    if not init_schema():
        return None

    expires_at = None
    if expires_in_days and expires_in_days > 0:
        expires_at = datetime.now(timezone.utc) + timedelta(days=expires_in_days)

    # Boucle anti-collision (extrêmement rare avec 32^12 combinaisons)
    for _ in range(8):
        code = _make_code()
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO premium_codes
                          (code, tier, duration_days, recipient_user_id,
                           created_by, expires_at, note)
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (code, tier, duration_days, recipient_user_id,
                         created_by, expires_at, note),
                    )
                conn.commit()
            return code
        except Exception as e:
            # Conflit PK = collision — on retente
            if "duplicate key" in str(e).lower() or "premium_codes_pkey" in str(e).lower():
                continue
            logger.error(f"❌ generate_code : {e}", exc_info=True)
            return None
    return None


def list_codes_for_user(user_id: int) -> list[dict]:
    """Liste les codes valides (non utilisés, non expirés) destinés à un user."""
    if not init_schema():
        return []
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT code, tier, duration_days, created_at, expires_at, note
                    FROM premium_codes
                    WHERE recipient_user_id = %s
                      AND redeemed_at IS NULL
                      AND (expires_at IS NULL OR expires_at > NOW())
                    ORDER BY created_at DESC
                    LIMIT 25
                    """,
                    (user_id,),
                )
                rows = cur.fetchall()
        return [
            {
                "code": r[0], "tier": r[1], "duration_days": r[2],
                "created_at": r[3], "expires_at": r[4], "note": r[5],
            }
            for r in rows
        ]
    except Exception as e:
        logger.error(f"❌ list_codes_for_user : {e}", exc_info=True)
        return []


def get_code(code: str) -> Optional[dict]:
    """Lit un code (utilisé ou non). Retourne None si introuvable."""
    if not init_schema():
        return None
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    SELECT code, tier, duration_days, recipient_user_id,
                           created_by, created_at, expires_at, redeemed_at,
                           redeemed_by_user_id, redeemed_by_guild_id, note
                    FROM premium_codes
                    WHERE code = %s
                    """,
                    (code.upper().strip(),),
                )
                r = cur.fetchone()
        if not r:
            return None
        return {
            "code": r[0], "tier": r[1], "duration_days": r[2],
            "recipient_user_id": r[3], "created_by": r[4],
            "created_at": r[5], "expires_at": r[6],
            "redeemed_at": r[7], "redeemed_by_user_id": r[8],
            "redeemed_by_guild_id": r[9], "note": r[10],
        }
    except Exception as e:
        logger.error(f"❌ get_code : {e}", exc_info=True)
        return None


def redeem_code(code: str, redeemer_user_id: int, target_guild_id: int) -> dict:
    """Applique un code Premium à un serveur cible — version atomique avec compensation.

    Stratégie pour éviter les états incohérents (Premium accordé mais code non
    marqué, ou inversement) :

    1. SELECT préliminaire pour diagnostiquer un éventuel échec (donne un
       message d'erreur précis : not_found / already_redeemed / expired / …).
    2. UPDATE atomique conditionnel `WHERE redeemed_at IS NULL` qui réserve le
       code en une seule opération (impossible qu'un autre redeem concurrent
       réussisse sur le même code).
    3. Application du Premium via `grant_premium()` (idempotent côté JSON).
    4. Si l'application échoue → COMPENSATION : on libère le code en remettant
       `redeemed_at = NULL`, pour que le destinataire puisse réessayer.

    Retourne { 'ok': bool, 'reason': str, 'tier': str|None, 'expires': dt|None }.
    Côté appelant, c'est à lui de vérifier que `redeemer_user_id` a vraiment la
    permission « Gérer le serveur » sur `target_guild_id`.
    """
    if not init_schema():
        return {"ok": False, "reason": "service_unavailable"}

    code_norm = code.upper().strip()

    # 1) Diagnostic — pourquoi le code n'est-il pas applicable ?
    info = get_code(code_norm)
    if info is None:
        return {"ok": False, "reason": "not_found"}
    if info["redeemed_at"] is not None:
        return {"ok": False, "reason": "already_redeemed"}
    if info["expires_at"] is not None and info["expires_at"] < datetime.now(timezone.utc):
        return {"ok": False, "reason": "expired"}
    if info["recipient_user_id"] is not None and info["recipient_user_id"] != redeemer_user_id:
        return {"ok": False, "reason": "wrong_recipient"}

    # 2) Réservation atomique — qui gagne la course ?
    try:
        with _connect() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    UPDATE premium_codes
                    SET redeemed_at = NOW(),
                        redeemed_by_user_id = %s,
                        redeemed_by_guild_id = %s
                    WHERE code = %s
                      AND redeemed_at IS NULL
                      AND (expires_at IS NULL OR expires_at > NOW())
                      AND (recipient_user_id IS NULL OR recipient_user_id = %s)
                    RETURNING tier, duration_days
                    """,
                    (redeemer_user_id, target_guild_id, code_norm, redeemer_user_id),
                )
                row = cur.fetchone()
            conn.commit()
        if not row:
            # Quelqu'un l'a redeemé entre l'étape 1 et 2 — message générique
            return {"ok": False, "reason": "already_redeemed"}
        tier, duration_days = row
    except Exception as e:
        logger.error(f"❌ redeem_code: étape réservation : {e}", exc_info=True)
        return {"ok": False, "reason": "internal_error"}

    # 3) Application du Premium
    expires_dt = None
    if duration_days and duration_days > 0:
        expires_dt = datetime.now(timezone.utc) + timedelta(days=duration_days)
    try:
        grant_premium(
            target_id=target_guild_id,
            target_type="guild",
            tier=tier,
            expires=expires_dt,
            granted_by=redeemer_user_id,
        )
    except Exception as e:
        logger.error(f"❌ redeem_code: grant_premium a échoué, COMPENSATION : {e}",
                     exc_info=True)
        # 4) Compensation — on libère le code pour que l'utilisateur puisse réessayer
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE premium_codes
                        SET redeemed_at = NULL,
                            redeemed_by_user_id = NULL,
                            redeemed_by_guild_id = NULL
                        WHERE code = %s
                        """,
                        (code_norm,),
                    )
                conn.commit()
        except Exception as comp_e:
            # Si même la compensation échoue, on logge un warning critique : un
            # admin devra libérer le code à la main (mais le serveur n'a pas eu
            # le Premium, donc pas de fraude possible — juste un code "perdu").
            logger.critical(
                f"❌❌ redeem_code: COMPENSATION échouée pour code={code_norm} — "
                f"le code reste marqué utilisé alors que le Premium n'a pas été "
                f"accordé. Erreur : {comp_e}",
                exc_info=True,
            )
        return {"ok": False, "reason": "internal_error"}

    return {"ok": True, "reason": "ok", "tier": tier, "expires": expires_dt}
