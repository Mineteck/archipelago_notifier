# Archipelago Notifier

![Python](https://img.shields.io/badge/Python-3-blue?logo=python) ![Discord](https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord) ![Archipelago](https://img.shields.io/badge/Archipelago-Multiworld-informational)

**Archipelago Notifier** est un bot Discord écrit en Python qui surveille des parties multiworld [Archipelago](https://archipelago.gg/) et relaie automatiquement leurs événements dans des salons Discord.

Il associe les slots Archipelago à des membres Discord, suit plusieurs rooms simultanément et conserve l'état des parties pour faciliter leur suivi.

## Fonctionnalités

- 🔔 Notifications Discord lors de la réception d'objets, y compris les objets en attente.
- 💀 Suivi des morts **DeathLink** et comptage par joueur.
- 🏆 Détection des objectifs atteints et de la fin d'une room.
- 💡 Suivi des indices (*hints*) Archipelago.
- 📦 Gestion des événements groupés **Release / Collect**, avec limitation des notifications redondantes.
- 🌐 Surveillance de plusieurs rooms grâce à des connexions WebSocket asynchrones.
- 🔄 Reconnexion automatique après une perte de connexion.
- 👥 Association des slots Archipelago aux utilisateurs Discord.
- 💾 Sauvegarde de la configuration et de la progression dans `rooms.json`.
- 🛠️ Administration via des commandes slash Discord.

## Technologies

| Technologie | Utilisation |
| --- | --- |
| Python | Langage principal |
| discord.py | Bot Discord et commandes slash |
| websockets | Connexions aux serveurs Archipelago |
| asyncio | Surveillance concurrente des rooms |
| python-dotenv | Chargement du token depuis `.env` |
| JSON | Persistance de la configuration des rooms |

## Installation

### Prérequis

- Python 3 et `pip` (utiliser une version compatible avec `requirements.txt`).
- Un bot créé dans le [Discord Developer Portal](https://discord.com/developers/applications).
- Un serveur Discord sur lequel inviter le bot avec les autorisations nécessaires.
- Une room Archipelago accessible et au moins un slot valide pour s'y connecter.

### 1. Cloner le dépôt

```bash
git clone https://github.com/Mineteck/archipelago_notifier.git
cd archipelago_notifier
```

### 2. Créer un environnement virtuel

```bash
python -m venv .venv
source .venv/bin/activate
```

Sous Windows :

```powershell
.venv\Scripts\activate
```

### 3. Installer les dépendances

```bash
pip install -r requirements.txt
```

### 4. Configurer Discord

Copier le fichier d'exemple :

```bash
cp .env.example .env
```

Renseigner le token de votre bot dans `.env` :

```dotenv
DISCORD_TOKEN=votre_token_discord
```

**Ne publiez jamais votre token Discord.** Gardez `.env` hors du contrôle de version.

### 5. Démarrer le bot

```bash
python bot_AI.py
```

Au démarrage, le bot se connecte à Discord, synchronise ses commandes slash et relance la surveillance des rooms enregistrées qui ne sont pas terminées.

## Utilisation

### Ajouter une room

Dans le salon Discord où vous souhaitez recevoir les notifications :

```text
/add_room port:<port> slot_name:<nom_du_slot> game:<jeu> password:<mot_de_passe_optionnel>
```

Le paramètre `port` correspond au port de la room hébergée sur `archipelago.gg`. Le bot construit l'adresse `wss://archipelago.gg:<port>`.

Cette commande nécessite la permission Discord **Gérer le serveur**.

### Associer d'autres joueurs

```text
/join port:<port> slot_name:<nom_du_slot> game:<jeu>
```

Le slot est associé au compte Discord qui lance la commande. Un paramètre `user` optionnel permet de désigner un autre membre.

### Consulter les rooms

```text
/rooms
```

Affiche les rooms enregistrées, les slots, les jeux, les membres associés et leur progression.

### Changer le salon de notifications

```text
/set_channel port:<port>
```

Définit le salon Discord courant comme destination des notifications de la room.

### Recharger la configuration

```text
/reload_rooms
```

Recharge la configuration enregistrée et démarre les surveillances nécessaires.

D'autres commandes de gestion sont définies dans [`commands.py`](commands.py).

## Architecture

```text
archipelago_notifier/
├── .github/workflows/     # Automatisations GitHub
├── .env.example           # Exemple de configuration du bot
├── archipelago_monitor.py # Client WebSocket et événements Archipelago
├── bot_AI.py              # Point d'entrée du bot Discord
├── commands.py            # Commandes slash Discord
├── monitor_manager.py     # Cycle de vie et reconnexion des moniteurs
├── notifications.py       # Construction et envoi des notifications
├── requirements.txt       # Dépendances Python
├── rooms_config.py        # Lecture et écriture des rooms
└── shared.py              # État et objets partagés
```

### Fonctionnement général

1. Le bot Discord démarre et synchronise ses commandes slash.
2. Les rooms configurées sont chargées depuis `rooms.json`.
3. Une tâche asynchrone est lancée pour chaque room active.
4. Chaque moniteur se connecte au serveur Archipelago par WebSocket avec un slot valide.
5. Les événements Archipelago sont interprétés puis transformés en notifications Discord.
6. La progression est sauvegardée ; une room entièrement terminée cesse d'être surveillée.

## Persistance et reconnexion

Les informations des rooms et des slots sont enregistrées dans `rooms.json`, notamment les associations Discord, le nombre de morts et l'état de complétion des objectifs.

En cas de déconnexion, le gestionnaire de moniteurs tente de rétablir la connexion. Les rooms déjà terminées ne sont pas redémarrées automatiquement.

## Sécurité

- Ne jamais committer `.env`, un token Discord ou des mots de passe réels.
- Limiter les permissions du bot Discord à celles nécessaires à son fonctionnement.
- **Attention :** le client WebSocket du projet désactive actuellement la vérification des certificats TLS pour les connexions sécurisées. Ce comportement est à corriger avant un déploiement exposé ou sensible.

## Contribution

Les suggestions et corrections peuvent être proposées via les [issues](https://github.com/Mineteck/archipelago_notifier/issues) et les pull requests du dépôt.

## Auteur

Projet développé par [Mineteck](https://github.com/Mineteck).

---

> Projet communautaire indépendant, non affilié officiellement à Archipelago ou Discord.
