"""
État global partagé entre tous les modules : instance du bot Discord,
configuration des rooms, et suivi des tâches de surveillance en cours.
Ce module ne dépend d'aucun autre module du bot -> pas de risque
d'import circulaire, tous les autres modules peuvent l'importer.
"""

import logging
import os
from pathlib import Path

import discord
from discord.ext import commands
from dotenv import load_dotenv

from rooms_config import RoomsConfig

load_dotenv()

DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")
ROOMS_FILE = Path("rooms.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ap-discord-bot")

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

rooms_config = RoomsConfig(ROOMS_FILE)

# gardé en mémoire pour pouvoir stopper/redémarrer les tâches de surveillance
monitor_tasks: dict = {}
# instances actives des moniteurs (ArchipelagoMonitor), pour pouvoir leur
# envoyer des requêtes (ex: demander les hints) depuis les commandes slash
active_monitors: dict = {}