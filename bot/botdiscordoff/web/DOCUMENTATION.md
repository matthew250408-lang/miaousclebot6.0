# 🎮 Guide d'Utilisation - Dashboard Miaouscle Bot

**Créateur:** matix  
**Version:** 3.3  
**Dernière mise à jour:** 30 Novembre 2025

---

## 📌 Table des matières

1. [🔐 Authentification](#authentification)
2. [🎯 Navigation](#navigation)
3. [⚙️ Configuration Générale](#configuration-générale)
4. [💬 Configuration des Messages](#configuration-des-messages)
5. [🤖 Configuration Automod](#configuration-automod)
6. [📍 Configuration des Logs](#configuration-des-logs)
7. [🔄 Réinitialiser la Configuration](#réinitialiser-la-configuration)
8. [❓ FAQ](#faq)

---

## 🔐 Authentification

### Comment se connecter?

1. Cliquez sur **"Se Connecter avec Discord"**
2. Autorisez l'application à accéder à vos serveurs Discord
3. Vous serrez redirigé vers le dashboard

### Qui peut accéder au dashboard?

- ✅ **Admins de serveur** - Peuvent configurer LEURS serveurs
- ✅ **Owner du bot** - Peut voir ET configurer TOUS les serveurs

### Que faire si je n'ai pas accès à un serveur?

Vous devez être **administrateur** du serveur Discord pour pouvoir le configurer. Contactez l'owner du serveur pour obtenir les permissions.

---

## 🎯 Navigation

### Sélectionner un serveur

1. Cliquez sur le serveur dans la **liste de gauche**
2. 🟢 **Vert** = Bot présent sur le serveur
3. 🔴 **Rouge** = Bot absent du serveur

### Ajouter le bot à votre serveur

Si le bot n'est pas sur votre serveur:
1. Un message apparaît: "🤖 Bot Absent"
2. Cliquez sur **"➕ Ajouter le Bot"**
3. Acceptez les permissions
4. Attendez quelques secondes
5. Rechargez la page

### Les 5 Onglets du Dashboard

| Onglet | Description |
|--------|-------------|
| ⚙️ Général | Langue, Rôle Booster |
| 💬 Messages | Canaux et messages de bienvenue/au revoir |
| 🤖 Automod | Configuration de la modération automatique |
| 📍 Logs | Canal logs et notifications configurables |
| 🔄 Reset | Réinitialiser tous les paramètres |

---

## ⚙️ Configuration Générale

### 🌍 Langue

Choisissez la langue du bot sur votre serveur:
- 🇫🇷 Français (FR)
- 🇬🇧 Anglais (EN)
- 🇪🇸 Espagnol (ES)
- 🇩🇪 Allemand (DE)

**Sauvegarde:** Le changement de langue est enregistré automatiquement.

### 🚀 Rôle Booster XP

Sélectionnez un rôle Discord qui donnera un boost XP:
1. Cliquez sur la liste déroulante
2. Sélectionnez un rôle
3. Cliquez sur **"💾 Enregistrer"**

---

## 💬 Configuration des Messages

### 👋 Canal Bienvenue

Où envoyer le message de bienvenue quand quelqu'un rejoint?
1. Sélectionnez un canal dans la liste déroulante
2. Les messages seront envoyés automatiquement

### 📩 Message Bienvenue

Personnalisez le message de bienvenue:
1. Cliquez sur le champ de texte
2. Écrivez votre message
3. Cliquez sur **"💾 Enregistrer"**

### 👋 Canal Au Revoir

Où envoyer le message d'au revoir quand quelqu'un quitte?
1. Sélectionnez un canal
2. C'est facultatif

### 📤 Message Au Revoir

Personnalisez le message d'au revoir:
1. Écrivez votre message
2. Cliquez sur **"💾 Enregistrer"**

---

## 🤖 Configuration Automod

### 🤖 Automod Actif

Activez ou désactivez la modération automatique:
- ✅ **Coché** = Automod activé
- ❌ **Décoché** = Automod désactivé

### ⚠️ Action à Appliquer

Choisissez ce que le bot fait quand il détecte du spam:
- **Supprimer le message** - Supprime immédiatement
- **Avertir l'utilisateur** - Envoie un avertissement
- **Mute l'utilisateur** - Rend muet temporairement

### ⏱️ Durée Mute

Si vous choisissez "Mute", définissez la durée en minutes:
- Par défaut: **30 minutes**
- Vous pouvez modifier cette valeur

### 🔔 Canal Automod

Où envoyer les notifications automod?
1. Sélectionnez un canal
2. Les alertes seront envoyées ici

---

## 📍 Configuration des Logs

### 📺 Canal Logs

Où envoyer les logs de modération?
1. Sélectionnez un canal
2. Tous les logs seront centralisés ici

### 🔔 Notifications Actives

Choisissez quels événements logger:

| Notification | Description |
|--------------|-------------|
| 🔨 Bannissements | Quand quelqu'un est banni |
| 🔓 Débannissements | Quand quelqu'un est débanni |
| 👢 Expulsions | Quand quelqu'un est expulsé |
| ⚠️ Avertissements | Quand quelqu'un reçoit un avertissement |
| 🔇 Mutes | Quand quelqu'un est rendu muet |
| 🔊 Démutes | Quand quelqu'un retrouve la parole |
| 🔒 Verrouillages | Quand un canal est verrouillé |
| 🔓 Déverrouillages | Quand un canal est déverrouillé |
| 🧹 Suppressions | Quand des messages sont supprimés |

### ✅ Comment activer/désactiver une notification?

1. **Cochez la case** pour activer
2. **Une popup de confirmation** apparaît
3. Cliquez **"OK"** pour confirmer
4. Si vous refusez → la case revient à son état précédent
5. Les changements sont automatiquement enregistrés

---

## 🔄 Réinitialiser la Configuration

### ⚠️ Attention - Action Irréversible!

**Cliquez UNIQUEMENT si vous êtes sûr!**

Cet onglet permet de:
- Réinitialiser TOUS les paramètres
- Revenir aux valeurs par défaut
- Cette action **NE PEUT PAS être annulée**

### Comment réinitialiser?

1. Allez à l'onglet **"🔄 Reset"**
2. Lisez l'avertissement
3. Cliquez sur **"🔄 Réinitialiser Maintenant"**
4. Une confirmation s'affiche
5. Cliquez **"OK"** pour confirmer

---

## 💾 Sauvegarder vos Modifications

### Banneau de Sauvegarde

Quand vous modifiez quelque chose:
1. Un **banneau gris** apparaît en bas
2. Il affiche: **"💾 Modifications non enregistrées"**
3. Deux boutons:
   - **💾 Enregistrer** - Sauvegarde les changements
   - **❌ Annuler** - Annule et revient à l'état précédent

### Confirmation de Sauvegarde

Après avoir cliqué **"Enregistrer"**:
1. Le banneau devient **vert** ✅
2. Affiche: **"✅ Modifications enregistrées!"**
3. Disparaît automatiquement après 2 secondes
4. Le serveur est mis à jour

---

## 📊 Les Logs dans Discord

### Où sont les logs?

Les logs sont envoyés au **Canal Logs** que vous avez configuré.

### Format des Logs

```
⚙️ Configuration Mise à Jour
👤 matix
🖥️ Serveur: 1188506879052238988

📋 Changements:
🌍 Langue: (vide) → ✅ `en`
🚀 Rôle Booster: `Ancien Rôle` → ✂️ Retiré
```

### Couleurs des Logs

- 🟢 **Vert** = Ajout/Activation
- 🔴 **Red** = Suppression/Désactivation
- 🟣 **Purple** = Modification

---

## ❓ FAQ

### Q: Comment savoir si mes modifications sont sauvegardées?

**R:** Cherchez le **banneau vert ✅** en bas. Si vous le voyez, c'est sauvegardé!

### Q: Puis-je configurer plusieurs serveurs?

**R:** Oui! Sélectionnez un autre serveur dans la liste et configurez-le.

### Q: Que faire si le bot ne répond pas?

**R:** 
1. Vérifiez que le bot est présent (🟢 vert)
2. Vérifiez les permissions Discord du bot
3. Contactez matix

### Q: Puis-je utiliser le dashboard sur mobile?

**R:** Oui! Le dashboard est **responsive** et fonctionne sur desktop ET mobile.

### Q: Comment se déconnecter?

**R:** Cliquez sur votre profil et sélectionnez **"Se Déconnecter"**.

### Q: Est-ce que le dashboard est sécurisé?

**R:** Oui!
- ✅ OAuth2 Discord (authentification sécurisée)
- ✅ Les tokens ne sont jamais stockés
- ✅ Permissions vérifiées

### Q: Les changements sont appliqués immédiatement?

**R:** Oui! Les changements sont appliqués:
- ✅ **Immédiatement** sur Discord
- ✅ **Enregistrés** dans la base de données
- ✅ **Loggés** si configuré

---

## 🎨 Conseils de Configuration

### Configuration Minimale Recommandée

1. ✅ Sélectionnez un **Canal Logs**
2. ✅ Activez les **Notifications essentielles** (Ban, Kick, Warn)
3. ✅ Configurez la **Langue** de votre serveur
4. ✅ Définissez un **Rôle Booster**

### Astuces

- 💡 Utilisez un **canal privé modérateur** pour les logs
- 💡 Activez toutes les notifications pour **une traçabilité complète**
- 💡 Testez l'automod sur un **canal de test** en premier
- 💡 Personnalisez les messages de bienvenue pour **accueillir les nouveaux**

---

## 📞 Support

**Créateur:** matix  
**Version Dashboard:** 3.3  
**Dernière mise à jour:** 30 Novembre 2025  

Pour toute question ou problème, contactez **matix**.

---

**Merci d'utiliser Miaouscle Bot!** 🎮✨
