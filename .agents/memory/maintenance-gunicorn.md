---
name: Maintenance sous Gunicorn
description: Contrainte de démarrage et de synchronisation temporelle du vérificateur de maintenance du dashboard.
---

La maintenance planifiée du dashboard est servie par plusieurs workers Gunicorn : le vérificateur doit être lancé lors du chargement du module `app:app`, puis utiliser un verrou de fichier pour qu’un seul worker exécute la boucle. Les dates doivent rester timezone-aware et être comparées en UTC.

**Why:** Le bloc `if __name__ == "__main__"` n’est pas exécuté par Gunicorn, et convertir des dates UTC en dates locales naïves peut déclencher ou arrêter une maintenance au mauvais moment.

**How to apply:** Conserver un démarrage idempotent par processus, une élection de leader par verrou de fichier sous `web/data`, et une compatibilité de lecture avec l’ancien format plat de maintenance si des clients l’utilisent encore.