"""
Verrou de singleton pour le bot Discord.

Empêche deux instances du bot de tourner simultanément avec le même token
Discord (sinon Discord envoie chaque événement aux deux gateways → doublons :
deux messages de bienvenue, deux logs, deux récompenses XP, etc.).

Mécanique :
    - Au boot, le bot appelle `acquire_or_exit()` qui :
        1. Ouvre une connexion PostgreSQL dédiée (ne sera jamais relâchée).
        2. Tente `pg_try_advisory_lock(LOCK_KEY)` (lock session-level, exclusif).
        3. Si la lock est libre → le bot continue son démarrage.
        4. Si la lock est prise → le bot logue un message clair et exit(0).
    - À la mort du processus (kill, crash, déconnexion réseau), PostgreSQL
      libère automatiquement la lock dès qu'il détecte la connexion morte
      (TCP keepalive). Aucun cleanup manuel nécessaire.
    - La connexion est gardée vivante via `keepalives` TCP (paramètres ci-dessous).

LOCK_KEY :
    Une constante BIGINT arbitraire mais stable. Si tu as plusieurs bots
    différents qui partagent la même DB, change cette clé pour chacun.

Comportement si DATABASE_URL est absent :
    Pas de protection — le bot démarre quand même (mode dégradé). Loggué en
    warning. Acceptable pour les environnements éphémères de test.
"""

import logging
import os
import signal
import sys
import threading
import time
from typing import Optional

logger = logging.getLogger(__name__)

# Clé arbitraire BIGINT, doit rester identique entre toutes les instances du bot
LOCK_KEY = 9847362157432918567 % (2**63)

_lock_conn = None  # Connexion gardée vivante en module-level pour éviter le GC
_keepalive_thread: Optional[threading.Thread] = None


def _get_dsn() -> Optional[str]:
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        return None
    if dsn.startswith("postgres://"):
        dsn = "postgresql://" + dsn[len("postgres://"):]
    return dsn


def _keepalive_loop(conn) -> None:
    """Pinge la connexion toutes les 30s pour qu'elle reste considérée vivante.
    Sans ça, certains pare-feux (NAT, etc.) coupent la connexion silencieusement
    après quelques minutes d'inactivité, ce qui libérerait la lock."""
    while True:
        try:
            time.sleep(30)
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        except Exception as e:
            logger.error(
                f"⚠️ Singleton lock: connexion DB morte ({e}). "
                "Demande un shutdown gracieux pour éviter une 2ème instance Discord.",
            )
            # Shutdown gracieux : SIGTERM permet à discord.py de fermer la
            # gateway, fermer la DB, sauver l'état. Un handler par défaut
            # ou installé par l'app traduit ça en KeyboardInterrupt.
            try:
                os.kill(os.getpid(), signal.SIGTERM)
            except Exception:
                # Dernier recours si SIGTERM échoue
                os._exit(1)
            return


def acquire_or_exit() -> None:
    """Tente de prendre le verrou singleton. Exit le processus si déjà pris.

    À appeler une seule fois, tout au début du boot du bot, AVANT bot.run().
    """
    global _lock_conn, _keepalive_thread

    dsn = _get_dsn()
    if not dsn:
        logger.warning(
            "⚠️ DATABASE_URL absent — pas de protection singleton. "
            "Risque de doublons si plusieurs instances du bot tournent."
        )
        return

    try:
        import psycopg
    except ImportError:
        logger.warning("⚠️ psycopg non installé — pas de protection singleton.")
        return

    try:
        conn = psycopg.connect(
            dsn,
            connect_timeout=10,
            keepalives=1,
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=3,
            autocommit=True,
        )
    except Exception as e:
        logger.error(f"⚠️ Singleton lock: connexion DB échouée ({e}) — démarrage sans protection.")
        return

    # Conversion bigint → (classid, objid) pour pg_locks
    classid_unsigned = (LOCK_KEY >> 32) & 0xFFFFFFFF
    objid_unsigned = LOCK_KEY & 0xFFFFFFFF
    classid = classid_unsigned - 0x100000000 if classid_unsigned >= 0x80000000 else classid_unsigned
    objid = objid_unsigned - 0x100000000 if objid_unsigned >= 0x80000000 else objid_unsigned

    def _try_acquire(c) -> bool:
        with c.cursor() as cur:
            cur.execute("SELECT pg_try_advisory_lock(%s)", (LOCK_KEY,))
            row = cur.fetchone()
            return bool(row[0]) if row else False

    def _holder_info(c) -> tuple[str, list[int]]:
        """Retourne (description lisible, liste des PIDs PG backend détenteurs)."""
        try:
            with c.cursor() as cur:
                cur.execute(
                    """
                    SELECT pid, application_name, client_addr, backend_start, state
                    FROM pg_stat_activity
                    WHERE pid IN (
                        SELECT pid FROM pg_locks
                        WHERE locktype = 'advisory' AND classid = %s AND objid = %s
                    )
                    """,
                    (classid, objid),
                )
                holders = cur.fetchall()
                desc = ", ".join(f"PID {h[0]} from {h[2]} since {h[3]} (state={h[4]})" for h in holders) or "inconnu"
                pids = [h[0] for h in holders]
                return desc, pids
        except Exception:
            return "inconnu", []

    # Stratégie de redémarrage gracieux : on tente le verrou, et s'il est pris
    # par une instance fantôme (ex: ancienne instance que Replit vient de tuer
    # mais dont la connexion PG n'a pas encore expiré), on retry avec backoff.
    # Au bout du timeout, on tente un takeover forcé via pg_terminate_backend
    # avant d'abandonner. Ça stabilise la prod pendant les redéploiements.
    MAX_WAIT_SECONDS = int(os.environ.get("SINGLETON_LOCK_WAIT_SECONDS", "90"))
    backoff = 5
    elapsed = 0
    acquired = False
    holder_desc = "inconnu"
    holder_pids: list[int] = []
    try:
        acquired = _try_acquire(conn)
        if not acquired:
            holder_desc, holder_pids = _holder_info(conn)
            logger.warning(
                f"⏳ Verrou pris par une autre instance ({holder_desc}). "
                f"Retry pendant {MAX_WAIT_SECONDS}s — probable redéploiement en cours."
            )
        while not acquired and elapsed < MAX_WAIT_SECONDS:
            time.sleep(backoff)
            elapsed += backoff
            acquired = _try_acquire(conn)
            if acquired:
                logger.info(f"✅ Verrou libéré après {elapsed}s d'attente — instance précédente terminée proprement.")
                break
            backoff = min(backoff * 2, 20)
        # Dernière tentative : forcer le takeover si l'ancienne backend est encore là
        if not acquired:
            holder_desc, holder_pids = _holder_info(conn)
            if holder_pids:
                logger.warning(
                    f"⚠️ Verrou toujours pris après {MAX_WAIT_SECONDS}s par {holder_desc}. "
                    f"Tentative de takeover forcé via pg_terminate_backend({holder_pids})."
                )
                try:
                    with conn.cursor() as cur:
                        for pid in holder_pids:
                            cur.execute("SELECT pg_terminate_backend(%s)", (pid,))
                    time.sleep(2)
                    acquired = _try_acquire(conn)
                    if acquired:
                        logger.info("✅ Takeover forcé réussi — verrou acquis.")
                except Exception as te:
                    logger.error(f"❌ Takeover forcé échoué : {te}")
    except Exception as e:
        logger.error(f"⚠️ Singleton lock: erreur SQL ({e}) — démarrage sans protection.")
        try:
            conn.close()
        except Exception:
            pass
        return

    if not acquired:
        try:
            conn.close()
        except Exception:
            pass
        msg = (
            "❌ Une autre instance du bot Miaouscle est déjà en cours d'exécution "
            f"(détenteur du verrou : {holder_desc}).\n"
            "   Discord ne tolère qu'une seule connexion gateway par token — démarrer une 2ème "
            "instance entraîne des doublons (messages de bienvenue, logs, XP). "
            f"Abandon après {MAX_WAIT_SECONDS}s d'attente + tentative de takeover."
        )
        logger.error(msg)
        print(msg, file=sys.stderr)
        sys.exit(0)

    # Lock acquise — on garde la connexion vivante en module-level + thread keepalive
    _lock_conn = conn
    _keepalive_thread = threading.Thread(
        target=_keepalive_loop, args=(conn,), daemon=True, name="singleton-lock-keepalive"
    )
    _keepalive_thread.start()
    logger.info(f"✅ Singleton lock acquise (key={LOCK_KEY}) — cette instance est unique.")
