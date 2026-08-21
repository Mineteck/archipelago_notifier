"""
Bot Discord <-> Archipelago

Fonctionnalités :
  - /link <slot_name>        : lie TON compte Discord à un nom de slot AP
  - /unlink <slot_name>      : supprime une liaison
  - /links                   : liste les liaisons connues
  - Écoute le serveur Archipelago en tâche de fond et, quand un slot lié
    reçoit un item, ping le membre Discord correspondant dans un salon dédié.

Installation :
    pip install discord.py websockets

Configuration : voir la section CONFIG ci-dessous.

Usage :
    python ap_discord_bot.py
"""

import asyncio
import json
import logging
import ssl
from pathlib import Path

import discord
from discord import app_commands
from discord.ext import commands
import websockets
from dotenv import load_dotenv
import os

load_dotenv()
# ---------------------------------------------------------------------------
# CONFIG - à adapter
# ---------------------------------------------------------------------------

DISCORD_BOT_TOKEN = os.getenv("DISCORD_TOKEN")
NOTIFY_CHANNEL_ID = int(os.getenv("CHANNEL_ID"))


ROOMS_FILE = Path("rooms.json")
DEATHS_FILE = Path("deaths.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ap-discord-bot")

# ---------------------------------------------------------------------------
# Config des rooms
# ---------------------------------------------------------------------------


class RoomsConfig:
    """Charge et recharge rooms.json à la demande."""

    def __init__(self, path: Path):
        self.path = path
        self.rooms: dict = {}
        self.load()

    def load(self):
        if not self.path.exists():
            log.warning("%s introuvable, aucune room configurée.", self.path)
            self.rooms = {}
            return
        text = self.path.read_text(encoding="utf-8").strip()
        if not text:
            log.warning("%s est vide, aucune room configurée.", self.path)
            self.rooms = {}
            return
        try:
            self.rooms = json.loads(text)
        except json.JSONDecodeError as e:
            log.error("%s est invalide (%s), aucune room chargée.", self.path, e)
            self.rooms = {}
            return
        log.info("Config chargée : %d room(s)", len(self.rooms))

    def server_urls(self) -> list[str]:
        return list(self.rooms.keys())

    def password_for(self, server_url: str) -> str:
        return self.rooms.get(server_url, {}).get("password", "") or ""

    def slots_for(self, server_url: str) -> dict:
        return self.rooms.get(server_url, {}).get("slot", {})

    def user_id_for(self, server_url: str, slot_name: str) -> int | None:
        entry = self.slots_for(server_url).get(slot_name)
        return entry.get("user_id") if entry else None

    def game_for(self, server_url: str, slot_name: str) -> str | None:
        entry = self.slots_for(server_url).get(slot_name)
        return entry.get("game") if entry else None

    def any_slot_name(self, server_url: str) -> str | None:
        """Un nom de slot quelconque pour cette room, utilisé pour la connexion
        de surveillance (le protocole AP exige un slot réel pour se connecter)."""
        slots = self.slots_for(server_url)
        return next(iter(slots), None)

    def games_for(self, server_url: str) -> list[str]:
        return sorted({v["game"] for v in self.slots_for(server_url).values()})


rooms_config = RoomsConfig(ROOMS_FILE)


class DeathCounter:
    """Compteur de morts DeathLink, persisté sur disque.

    Structure interne : {server_url: {slot_name: count}}
    """

    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, dict[str, int]] = {}
        self.load()

    def load(self):
        if self.path.exists():
            text = self.path.read_text(encoding="utf-8").strip()
            if text:
                try:
                    self.data = json.loads(text)
                except json.JSONDecodeError as e:
                    log.error("%s est corrompu (%s), on repart d'un compteur vide.", self.path, e)
                    self.data = {}
            else:
                self.data = {}

    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2, ensure_ascii=False), encoding="utf-8")

    def increment(self, server_url: str, slot_name: str) -> int:
        room = self.data.setdefault(server_url, {})
        room[slot_name] = room.get(slot_name, 0) + 1
        self.save()
        return room[slot_name]

    def total_for_room(self, server_url: str) -> int:
        return sum(self.data.get(server_url, {}).values())

    def total_all(self) -> int:
        return sum(sum(room.values()) for room in self.data.values())

    def counts_for_room(self, server_url: str) -> dict[str, int]:
        return self.data.get(server_url, {})

    def reset(self, server_url: str | None = None):
        if server_url is None:
            self.data = {}
        else:
            self.data.pop(server_url, None)
        self.save()


death_counter = DeathCounter(DEATHS_FILE)

# ---------------------------------------------------------------------------
# Bot Discord
# ---------------------------------------------------------------------------

intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)

# gardé en mémoire pour pouvoir stopper/redémarrer les tâches de surveillance
monitor_tasks: dict[str, asyncio.Task] = {}


@bot.event
async def on_ready():
    log.info("Bot connecté en tant que %s", bot.user)
    await bot.tree.sync()
    start_all_monitors()


def start_all_monitors():
    for server_url in rooms_config.server_urls():
        if server_url in monitor_tasks and not monitor_tasks[server_url].done():
            continue
        monitor_tasks[server_url] = bot.loop.create_task(archipelago_loop(server_url))


@bot.tree.command(name="reload_rooms", description="Recharge rooms.json et (re)démarre les surveillances manquantes")
async def reload_rooms_cmd(interaction: discord.Interaction):
    rooms_config.load()
    start_all_monitors()
    await interaction.response.send_message(
        f"🔄 Config rechargée : {len(rooms_config.server_urls())} room(s) surveillée(s).",
        ephemeral=True,
    )


@bot.tree.command(name="rooms", description="Liste les rooms et slots actuellement surveillés")
async def rooms_cmd(interaction: discord.Interaction):
    if not rooms_config.rooms:
        await interaction.response.send_message("Aucune room configurée dans rooms.json.", ephemeral=True)
        return

    lines = []
    for server_url in rooms_config.server_urls():
        lines.append(f"**{server_url}**")
        for slot_name, info in rooms_config.slots_for(server_url).items():
            lines.append(f"  • {slot_name} ({info.get('game')}) → <@{info.get('user_id')}>")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="deaths", description="Affiche le compteur de morts DeathLink")
async def deaths_cmd(interaction: discord.Interaction):
    if not rooms_config.rooms:
        await interaction.response.send_message("Aucune room configurée.", ephemeral=True)
        return

    lines = []
    for server_url in rooms_config.server_urls():
        counts = death_counter.counts_for_room(server_url)
        room_total = death_counter.total_for_room(server_url)
        lines.append(f"**{server_url}** — {room_total} mort(s) au total")
        if counts:
            for slot_name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"  💀 {slot_name} : {count}")
        else:
            lines.append("  _Aucune mort enregistrée pour l'instant_")

    lines.append(f"\n**Total général : {death_counter.total_all()} mort(s)**")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="reset_deaths", description="Remet à zéro le compteur de morts DeathLink")
async def reset_deaths_cmd(interaction: discord.Interaction):
    death_counter.reset()
    await interaction.response.send_message("🔄 Compteur de morts remis à zéro.", ephemeral=True)


async def notify_item_received(server_url: str, receiver_slot: str, item_name: str, sender_name: str):
    channel = bot.get_channel(NOTIFY_CHANNEL_ID)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", NOTIFY_CHANNEL_ID)
        return

    user_id = rooms_config.user_id_for(server_url, receiver_slot)
    mention = f"<@{user_id}>" if user_id else f"**{receiver_slot}**"

    await channel.send(f"🎁 {mention} a reçu **{item_name}** (envoyé par {sender_name})")


async def notify_death(server_url: str, slot_name: str, cause: str | None):
    channel = bot.get_channel(NOTIFY_CHANNEL_ID)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", NOTIFY_CHANNEL_ID)
        return

    user_id = rooms_config.user_id_for(server_url, slot_name)
    mention = f"<@{user_id}>" if user_id else f"**{slot_name}**"

    new_count = death_counter.increment(server_url, slot_name)
    cause_txt = f" ({cause})" if cause else ""

    await channel.send(f"💀 {mention} est mort{cause_txt} — **{new_count}** mort(s) au compteur")


# ---------------------------------------------------------------------------
# Client Archipelago (une tâche de fond par room)
# ---------------------------------------------------------------------------


class ArchipelagoMonitor:
    """
    Se connecte à une room Archipelago avec un slot réel (le protocole AP
    l'exige) et suit TOUS les échanges d'items de la room via les messages
    PrintJSON de type 'ItemSend', diffusés à tous les clients connectés
    quel que soit leur slot.
    """

    def __init__(self, server_url: str, config: RoomsConfig):
        self.server_url = server_url
        self.config = config

        self.connect_slot_name = config.any_slot_name(server_url)
        self.connect_game = config.game_for(server_url, self.connect_slot_name) if self.connect_slot_name else None
        self.password = config.password_for(server_url)

        self.ws = None
        # item_id_to_name est indexé par jeu, car les IDs d'items sont
        # spécifiques à chaque jeu dans Archipelago.
        self.item_id_to_name: dict[str, dict[int, str]] = {}
        self.slot_names: dict[int, str] = {}  # slot number -> slot name

    async def run(self):
        if not self.connect_slot_name:
            log.error("Aucun slot défini pour %s, impossible de surveiller cette room.", self.server_url)
            return

        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE

        async with websockets.connect(
            self.server_url, ssl=ssl_ctx if self.server_url.startswith("wss") else None
        ) as ws:
            self.ws = ws
            log.info("[%s] Connecté au serveur Archipelago", self.server_url)
            async for raw in ws:
                print(raw)
                await self.handle_packet(json.loads(raw))

    async def send(self, packets: list):
        await self.ws.send(json.dumps(packets))

    async def handle_packet(self, packets):
        for pkt in packets:
            
            if "DeathLink" in pkt.get("tags", []):
                #await self.handle_bounce(pkt)
                print("FDP DE MMERDE")
            cmd = pkt.get("cmd")
            log.info("[%s] Paquet reçu: %s", self.server_url, cmd)
            log.info("[%s] Tags reçu: %s", self.server_url, pkt.get("tags", []))

            if cmd == "RoomInfo":
                # On demande le datapackage uniquement pour les jeux utilisés
                # dans cette room (tels que déclarés dans rooms.json).
                games = self.config.games_for(self.server_url)
                await self.send([{"cmd": "GetDataPackage", "games": games}])

            elif cmd == "DataPackage":
                for game_name, game_data in pkt["data"]["games"].items():
                    self.item_id_to_name[game_name] = {
                        v: k for k, v in game_data["item_name_to_id"].items()
                    }
                await self.connect_monitor()

            elif cmd == "Connected":
                self.slot_names = {p["slot"]: p["name"] for p in pkt["players"]}
                log.info("[%s] Surveillance active, %d joueur(s)", self.server_url, len(self.slot_names))

            elif cmd == "ConnectionRefused":
                log.error("[%s] Connexion refusée: %s", self.server_url, pkt.get("errors"))

            elif cmd == "PrintJSON":
                await self.handle_print_json(pkt)

            elif cmd == "Bounced": #c'est le deathlink askip mais c'est de la merde xD
                await self.handle_bounce(pkt)

    async def handle_print_json(self, pkt):
        print
        if pkt.get("type") != "ItemSend":
            return
        item_data = pkt.get("item", {})
        receiving_slot = pkt.get("receiving")
        sender_slot = item_data.get("player")

        receiver_name = self.slot_names.get(receiving_slot, f"Slot#{receiving_slot}")
        sender_name = self.slot_names.get(sender_slot, f"Slot#{sender_slot}")

        # Le namespace de l'item ID dépend du jeu du DESTINATAIRE
        receiver_game = self.config.game_for(self.server_url, receiver_name)
        game_items = self.item_id_to_name.get(receiver_game, {})
        item_name = game_items.get(item_data.get("item"), f"Item#{item_data.get('item')}")

        await notify_item_received(self.server_url, receiver_name, item_name, sender_name)

    async def handle_bounce(self, pkt):
        # Les paquets DeathLink sont des Bounce avec "DeathLink" dans les
        # tags, et contiennent data.source = nom du slot qui est mort.
        if "DeathLink" not in pkt.get("tags", []):
            return
        data = pkt.get("data", {})
        source = data.get("source")
        cause = data.get("cause")
        if not source:
            return
        await notify_death(self.server_url, source, cause)

    async def connect_monitor(self):
        await self.send([{
            "cmd": "Connect",
            "game": self.connect_game,
            "name": self.connect_slot_name,
            "password": self.password or None,
            "uuid": f"ap-discord-bot-monitor-{self.server_url}",
            "version": {"major": 0, "minor": 5, "build": 0, "class": "Version"},
            "items_handling": 0,
            "tags": ["TextOnly", "DeathLink"],
            "slot_data": False,
        }])


async def archipelago_loop(server_url: str):
    await bot.wait_until_ready()
    monitor = ArchipelagoMonitor(server_url, rooms_config)
    while not bot.is_closed():
        try:
            await monitor.run()
        except (websockets.ConnectionClosed, OSError) as e:
            log.warning("[%s] Connexion perdue (%s), nouvelle tentative dans 10s...", server_url, e)
            await asyncio.sleep(10)


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)