# Archipelago Notifier

![Python](https://img.shields.io/badge/Python-3-blue?logo=python) ![Discord](https://img.shields.io/badge/Discord-Bot-5865F2?logo=discord) ![Archipelago](https://img.shields.io/badge/Archipelago-Multiworld-informational)

**Archipelago Notifier** is a Python Discord bot that monitors [Archipelago](https://archipelago.gg/) multiworld sessions and automatically forwards game events to Discord channels.

It links Archipelago slots to Discord members, monitors multiple rooms simultaneously, and saves session progress to make games easier to follow.

## 🤖 Invite the Bot

Add **Archipelago Notifier** to your Discord server to receive notifications from your Archipelago sessions.

[![Invite the bot](https://img.shields.io/badge/Discord-Invite_the_Bot-5865F2?logo=discord&logoColor=white)](https://discord.com/oauth2/authorize?client_id=1540372862513455215)

## Features

- 🔔 Discord notifications for received items, including pending items.
- 💀 **DeathLink** event tracking and death counts per player.
- 🏆 Goal completion and room completion detection.
- 💡 Archipelago hint tracking.
- 📦 **Release / Collect** event handling, with reduced duplicate notifications.
- 🌐 Concurrent monitoring of multiple rooms through asynchronous WebSocket connections.
- 🔄 Automatic reconnection after connection loss.
- 👥 Archipelago slot-to-Discord user associations.
- 💾 Configuration and progress saved in `rooms.json`.
- 🛠️ Administration through Discord slash commands.

## Technologies

| Technology | Purpose |
| --- | --- |
| Python | Main programming language |
| discord.py | Discord bot and slash commands |
| websockets | Connections to Archipelago servers |
| asyncio | Concurrent room monitoring |
| python-dotenv | Loading the bot token from `.env` |
| JSON | Persistent room configuration |

## Installation

### Requirements

- Python 3 and `pip` (use a version compatible with `requirements.txt`).
- A bot created in the [Discord Developer Portal](https://discord.com/developers/applications).
- A Discord server where you can invite the bot with the required permissions.
- An accessible Archipelago room and at least one valid slot to connect with.

### 1. Clone the repository

```bash
git clone https://github.com/Mineteck/archipelago_notifier.git
cd archipelago_notifier
```

### 2. Create a virtual environment

```bash
python -m venv .venv
source .venv/bin/activate
```

On Windows:

```powershell
.venv\Scripts\activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Discord

Copy the example environment file:

```bash
cp .env.example .env
```

Set your bot token in `.env`:

```dotenv
DISCORD_TOKEN=your_discord_token
```

**Never publish your Discord token.** Keep `.env` out of version control.

### 5. Start the bot

```bash
python bot_AI.py
```

On startup, the bot connects to Discord, synchronizes its slash commands, and resumes monitoring saved rooms that have not been completed.

## Usage

### Add a room

In the Discord channel where you want to receive notifications:

```text
/add_room port:<port> slot_name:<slot_name> game:<game> password:<optional_password>
```

The `port` parameter is the port of the room hosted on `archipelago.gg`. The bot builds the address `wss://archipelago.gg:<port>`.

This command requires the Discord **Manage Server** permission.

### Link other players

```text
/join port:<port> slot_name:<slot_name> game:<game>
```

The slot is linked to the Discord account running the command. An optional `user` parameter lets you select another member.

### View rooms

```text
/rooms
```

Displays registered rooms, slots, games, linked members, and their progress.

### Change the notification channel

```text
/set_channel port:<port>
```

Sets the current Discord channel as the notification destination for the room.

### Reload configuration

```text
/reload_rooms
```

Reloads saved configuration and starts any required monitoring tasks.

Additional management commands are defined in [`commands.py`](commands.py).

## Project Structure

```text
archipelago_notifier/
├── .github/workflows/     # GitHub automation
├── .env.example           # Example bot configuration
├── archipelago_monitor.py # WebSocket client and Archipelago events
├── bot_AI.py              # Discord bot entry point
├── commands.py            # Discord slash commands
├── monitor_manager.py     # Monitor lifecycle and reconnection
├── notifications.py       # Notification formatting and delivery
├── requirements.txt       # Python dependencies
├── rooms_config.py        # Room configuration persistence
└── shared.py              # Shared state and objects
```

### How It Works

1. The Discord bot starts and synchronizes its slash commands.
2. Configured rooms are loaded from `rooms.json`.
3. An asynchronous task is started for each active room.
4. Each monitor connects to the Archipelago server via WebSocket using a valid slot.
5. Archipelago events are interpreted and turned into Discord notifications.
6. Progress is saved; fully completed rooms are no longer monitored.

## Persistence and Reconnection

Room and slot information is saved in `rooms.json`, including Discord associations, death counts, and goal completion status.

If a connection drops, the monitor manager attempts to reconnect. Completed rooms are not automatically restarted.

## Security

- Never commit `.env`, Discord tokens, or real passwords.
- Grant the bot only the Discord permissions it needs.
- **Warning:** the project's WebSocket client currently disables TLS certificate verification for secure connections. This should be fixed before deploying in a sensitive or exposed environment.

## 🆘 Support

Having trouble with Archipelago Notifier or want to suggest a feature?

- 🐛 [Report a bug](https://github.com/Mineteck/archipelago_notifier/issues/new?template=bug_report.yml)
- 💡 [Request a feature](https://github.com/Mineteck/archipelago_notifier/issues/new?template=feature_request.yml)
- 📖 [Browse the source code](https://github.com/Mineteck/archipelago_notifier)

## 📜 Legal Information

Use of **Archipelago Notifier** is subject to the following documents:

- 📋 [Terms of Service](https://github.com/Mineteck/archipelago_notifier/blob/main/TERMS_OF_SERVICE.md)
- 🔒 [Privacy Policy](https://github.com/Mineteck/archipelago_notifier/blob/main/PRIVACY_POLICY.md)

By using the bot, you agree to the Terms of Service and acknowledge the Privacy Policy.

## Contributing

Suggestions and fixes are welcome through [issues](https://github.com/Mineteck/archipelago_notifier/issues) and pull requests.

## Author

Developed by [Mineteck](https://github.com/Mineteck).

---

> Independent community project, not officially affiliated with Archipelago or Discord.
