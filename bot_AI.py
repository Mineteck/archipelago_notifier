"""
Point d'entrée du bot Discord <-> Archipelago.

Fichiers du projet :
  - shared.py              état global (bot, rooms_config, tâches actives)
  - rooms_config.py         lecture/écriture de rooms.json
  - notifications.py        envoi des messages Discord (items, morts, victoires)
  - archipelago_monitor.py  client Archipelago (une instance par room)
  - monitor_manager.py      démarrage/arrêt/reconnexion des surveillances
  - commands.py             toutes les commandes slash
  - main.py                 ce fichier : démarre le bot

Installation :
    pip install discord.py websockets python-dotenv

Usage :
    python main.py
"""

from shared import bot, log, DISCORD_BOT_TOKEN
from monitor_manager import start_all_monitors
import commands  # noqa: F401  (enregistre les commandes slash au chargement)


@bot.event
async def on_ready():
    log.info("Bot connecté en tant que %s", bot.user)
    await bot.tree.sync()
    start_all_monitors()


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)