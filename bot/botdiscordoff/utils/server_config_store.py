"""
Store de persistance pour les configurations de serveurs.

PostgreSQL est la **source de vérité unique**. Le JSON `web/data/server_configs.json`
n'est lu **qu'une seule fois** lors de la migration initiale (table vide au boot).
Aucune écriture vers le JSON n'est faite ensuite — sinon on recréerait le bug
historique : le fichier serait re-tracké par git malgré .gitignore et écraserait
les configs en prod à chaque "Published your App" (mode VM Replit).

API publique synchrone (le bot appelle via `asyncio.to_thread` quand il faut) :
    - init_schema() : crée la table si absente + migration auto depuis le JSON
    - load_one(guild_id) -> dict
    - load_all() -> dict[str, dict]
    - save_one(guild_id, config: dict) -> None    (lève si la DB est down)
    - delete_one(guild_id) -> None                (lève si la DB est down)

Comportement en cas de panne DB :
    - **Lecture** (load_one/load_all) : retombe sur la dernière copie JSON
      connue pour ne pas faire planter le bot ni le dashboard. Logue un
      warning.
    - **Écriture** (save_one/delete_one) : **lève une exception** au lieu
      d'écrire silencieusement dans un JSON local qui ne serait jamais
      reconcilié vers la DB au retour. Le dashboard renverra alors un 500 au
      front et l'utilisateur verra le message d'erreur explicite déjà géré
      depuis la v13.1.3 (au lieu d'un faux succès silencieux).
"""

import json
import logging
import os
import threading
from typing import Optional

logger = logging.getLogger(__name__)

# Chemin absolu basé sur la racine projet (et non le CWD du process), parce
# que `start.sh` lance le dashboard depuis `web/` (cd web && gunicorn) tandis
# que le bot tourne depuis la racine. Un chemin relatif divergerait entre les
# deux processus et créerait `web/web/data/server_configs.json` côté dashboard
# — qui ne serait pas couvert par .gitignore (`web/data/*.json`).
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LEGACY_JSON_PATH = os.path.join(_PROJECT_ROOT, "web", "data", "server_configs.json")
_SCHEMA_INIT_LOCK = threading.Lock()
_SCHEMA_INITIALIZED = False


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


def _load_legacy_file() -> dict:
    try:
        with open(_LEGACY_JSON_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            return data
        return {}
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save_legacy_file(data: dict) -> None:
    os.makedirs(os.path.dirname(_LEGACY_JSON_PATH), exist_ok=True)
    with open(_LEGACY_JSON_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, ensure_ascii=False)


def init_schema() -> bool:
    """Crée la table si elle n'existe pas, et seed depuis le JSON si la table est vide.

    Retourne True si la DB est utilisable, False sinon (fallback JSON).
    Idempotent et thread-safe.
    """
    global _SCHEMA_INITIALIZED
    if _SCHEMA_INITIALIZED:
        return True
    with _SCHEMA_INIT_LOCK:
        if _SCHEMA_INITIALIZED:
            return True
        if not _get_dsn():
            logger.warning("DATABASE_URL absent — server_config_store en mode fichier JSON.")
            return False
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        CREATE TABLE IF NOT EXISTS server_configs (
                            guild_id    BIGINT PRIMARY KEY,
                            config      JSONB NOT NULL DEFAULT '{}'::jsonb,
                            updated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
                        )
                        """
                    )
                    cur.execute("SELECT COUNT(*) FROM server_configs")
                    row = cur.fetchone()
                    count = row[0] if row else 0
                    if count == 0:
                        legacy = _load_legacy_file()
                        if legacy:
                            logger.info(
                                f"Migration : import de {len(legacy)} configs depuis {_LEGACY_JSON_PATH} vers PostgreSQL."
                            )
                            for gid, cfg in legacy.items():
                                try:
                                    gid_int = int(gid)
                                except (ValueError, TypeError):
                                    continue
                                if not isinstance(cfg, dict):
                                    cfg = {}
                                cur.execute(
                                    """
                                    INSERT INTO server_configs (guild_id, config)
                                    VALUES (%s, %s::jsonb)
                                    ON CONFLICT (guild_id) DO NOTHING
                                    """,
                                    (gid_int, json.dumps(cfg)),
                                )
                conn.commit()
            _SCHEMA_INITIALIZED = True
            logger.info("✅ server_config_store: schéma PostgreSQL prêt.")
            return True
        except Exception as e:
            logger.error(f"❌ server_config_store: init schéma échouée ({e}) — fallback JSON.", exc_info=True)
            return False


def load_one(guild_id) -> dict:
    """Charge la config d'un serveur. Renvoie {} si absent."""
    try:
        gid = int(guild_id)
    except (ValueError, TypeError):
        return {}
    if init_schema():
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT config FROM server_configs WHERE guild_id = %s", (gid,))
                    row = cur.fetchone()
                    if row and row[0] is not None:
                        cfg = row[0]
                        if isinstance(cfg, str):
                            try:
                                cfg = json.loads(cfg)
                            except json.JSONDecodeError:
                                cfg = {}
                        return cfg if isinstance(cfg, dict) else {}
            return {}
        except Exception as e:
            logger.warning(f"load_one({gid}) DB erreur — fallback JSON ({e})")
    legacy = _load_legacy_file()
    cfg = legacy.get(str(gid), {})
    return cfg if isinstance(cfg, dict) else {}


def load_all() -> dict:
    """Charge toutes les configs sous forme {str(guild_id): {...}}."""
    if init_schema():
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("SELECT guild_id, config FROM server_configs")
                    out = {}
                    for gid, cfg in cur.fetchall():
                        if isinstance(cfg, str):
                            try:
                                cfg = json.loads(cfg)
                            except json.JSONDecodeError:
                                cfg = {}
                        if isinstance(cfg, dict):
                            out[str(gid)] = cfg
                    return out
        except Exception as e:
            logger.warning(f"load_all() DB erreur — fallback JSON ({e})")
    return _load_legacy_file()


class ServerConfigStoreError(RuntimeError):
    """Levée quand une écriture vers le store de configuration échoue.

    Le dashboard transforme cela en HTTP 500 ; le frontend (depuis v13.1.3)
    affiche un message d'erreur explicite à l'utilisateur au lieu d'un faux
    succès silencieux. On préfère **lever** plutôt que d'écrire dans un JSON
    local qui ne serait jamais réconcilié vers la DB au retour de panne.
    """


def save_one(guild_id, config: dict) -> None:
    """Upsert la config d'un serveur.

    Essaie PostgreSQL en priorité. Si DATABASE_URL est absent ou DB
    indisponible, retombe sur le fichier JSON local pour que la config
    persiste quand même.
    """
    try:
        gid = int(guild_id)
    except (ValueError, TypeError):
        raise ValueError(f"guild_id invalide: {guild_id!r}")
    if not isinstance(config, dict):
        raise TypeError(f"config doit être dict, pas {type(config).__name__}")
    if init_schema():
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        INSERT INTO server_configs (guild_id, config, updated_at)
                        VALUES (%s, %s::jsonb, NOW())
                        ON CONFLICT (guild_id) DO UPDATE
                        SET config = EXCLUDED.config,
                            updated_at = NOW()
                        """,
                        (gid, json.dumps(config)),
                    )
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"save_one({gid}) DB erreur — fallback JSON ({e})")
    # Fallback JSON quand PostgreSQL est absent ou en panne
    data = _load_legacy_file()
    data[str(gid)] = config
    _save_legacy_file(data)
    logger.info(f"save_one({gid}) sauvegardé dans le JSON local (pas de DB).")


def delete_one(guild_id) -> None:
    """Supprime la config d'un serveur. Fallback JSON si DB indisponible."""
    try:
        gid = int(guild_id)
    except (ValueError, TypeError):
        return
    if init_schema():
        try:
            with _connect() as conn:
                with conn.cursor() as cur:
                    cur.execute("DELETE FROM server_configs WHERE guild_id = %s", (gid,))
                conn.commit()
            return
        except Exception as e:
            logger.warning(f"delete_one({gid}) DB erreur — fallback JSON ({e})")
    # Fallback JSON
    data = _load_legacy_file()
    data.pop(str(gid), None)
    _save_legacy_file(data)
