---
name: Paiements administrables
description: Règle de cohérence entre réglages admin, API checkout et interface Premium.
---

Les réglages admin de paiement ne doivent jamais être uniquement visuels : un fournisseur ou un plan désactivé doit être refusé par l’API, et chaque rafraîchissement de l’interface doit conserver ce blocage.

**Why:** la sélection d’un serveur ou d’un tier peut réinitialiser les boutons de paiement ; sans contrôle serveur et sans réapplication côté interface, un réglage admin peut sembler ignoré.

**How to apply:** lors de toute évolution du checkout, valider le fournisseur et le plan dans le serveur, exposer leur état effectif au front, puis réappliquer l’état après les changements de tier, serveur ou modal.