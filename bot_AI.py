"""
Bot Discord <-> Archipelago (multi-room, piloté par config)

Fonctionnalités :
  - Notifications d'items reçus (ItemSend)
  - Notifications de morts DeathLink (Bounced) avec compteur persistant
  - /hints <slot_name> : liste les hints connus concernant ce joueur
    (aussi bien les objets qu'il doit trouver pour d'autres que les
    hints déjà posés sur ses propres objets)
  - /add_room : ajoute une room, liée au serveur Discord où la commande
    est utilisée
  - /join : lie ton compte Discord à un slot d'une room déjà ajoutée

Toute la liaison slot <-> utilisateur Discord <-> jeu est définie dans un
fichier rooms.json.

Format de rooms.json :

{
    "wss://archipelago.gg:62811": {
        "guild_id": 123456789012345678,
        "password": "",
        "slot": {
            "NomDuSlot": {
                "user_id": 210844943609102336,
                "game": "Nom Exact Du Jeu"
            },
            ...
        }
    }
}

Installation :
    pip install discord.py websockets python-dotenv

Usage :
    python bot.py
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

ROOMS_FILE = Path("rooms.json")
DEATHS_FILE = Path("deaths.json")

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ap-discord-bot")

# ---------------------------------------------------------------------------
# Config des rooms
# ---------------------------------------------------------------------------


class RoomsConfig:
    """Charge, recharge et modifie rooms.json à la demande."""

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

    def save(self):
        self.path.write_text(json.dumps(self.rooms, indent=2, ensure_ascii=False), encoding="utf-8")

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

    def find_server_for_slot(self, slot_name: str) -> str | None:
        """Trouve dans quelle room un slot donné (nom, insensible à la casse) est défini."""
        target = slot_name.lower()
        for server_url in self.server_urls():
            for name in self.slots_for(server_url):
                if name.lower() == target:
                    return server_url
        return None

    def guild_id_for(self, server_url: str) -> int | None:
        return self.rooms.get(server_url, {}).get("guild_id")

    def channel_id_for(self, server_url: str) -> int | None:
        return self.rooms.get(server_url, {}).get("channel_id")

    def set_channel_id(self, server_url: str, channel_id: int) -> bool:
        if server_url not in self.rooms:
            return False
        self.rooms[server_url]["channel_id"] = channel_id
        self.save()
        return True

    def rooms_for_guild(self, guild_id: int | None) -> list[str]:
        return [url for url, data in self.rooms.items() if data.get("guild_id") == guild_id]

    def add_room(self, server_url: str, password: str, guild_id: int, channel_id: int) -> bool:
        """Ajoute une nouvelle room vide. Retourne False si elle existe déjà."""
        if server_url in self.rooms:
            return False
        self.rooms[server_url] = {
            "guild_id": guild_id,
            "channel_id": channel_id,
            "password": password,
            "slot": {},
        }
        self.save()
        return True

    def add_slot(self, server_url: str, slot_name: str, user_id: int, game: str) -> bool:
        """Ajoute ou met à jour un slot dans une room existante."""
        if server_url not in self.rooms:
            return False
        self.rooms[server_url].setdefault("slot", {})[slot_name] = {"user_id": user_id, "game": game}
        self.save()
        return True


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
# instances actives des moniteurs, pour pouvoir leur envoyer des requêtes
# (ex: demander les hints) depuis les commandes slash
active_monitors: dict[str, "ArchipelagoMonitor"] = {}


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


def restart_monitor(server_url: str):
    """Arrête (si besoin) puis relance la tâche de surveillance d'une room.
    Utile après /add_room ou /join car ArchipelagoMonitor fige son slot de
    connexion à sa création : il faut recréer l'instance pour qu'elle
    reprenne en compte les nouveaux slots ajoutés dans rooms.json."""
    task = monitor_tasks.get(server_url)
    if task and not task.done():
        task.cancel()
    active_monitors.pop(server_url, None)
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
        channel_id = rooms_config.channel_id_for(server_url)
        channel_txt = f" — notifs dans <#{channel_id}>" if channel_id else " — notifs dans le salon par défaut"
        lines.append(f"**{server_url}**{channel_txt}")
        for slot_name, info in rooms_config.slots_for(server_url).items():
            lines.append(f"  • {slot_name} ({info.get('game')}) → <@{info.get('user_id')}>")
    await interaction.response.send_message("\n".join(lines), ephemeral=True)


@bot.tree.command(name="add_room", description="Ajoute une room Archipelago (avec au moins un slot), liée à ce serveur Discord")
@app_commands.describe(
    server_url="Adresse WebSocket de la room, ex: wss://archipelago.gg:12345",
    slot_name="Ton nom de slot exact dans cette room (le premier joueur lié)",
    game="Le jeu tel que déclaré dans ton yaml",
    password="Mot de passe de la room (laisse vide si aucun)",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def add_room_cmd(
    interaction: discord.Interaction,
    server_url: str,
    slot_name: str,
    game: str,
    password: str = "",
):
    server_url = "wss://" + server_url
    if interaction.guild_id is None:
        await interaction.response.send_message(
            "Cette commande doit être utilisée depuis un serveur Discord.", ephemeral=True
        )
        return

    added = rooms_config.add_room(server_url, password, interaction.guild_id, interaction.channel_id)
    if not added:
        await interaction.response.send_message(
            f"⚠️ La room **{server_url}** est déjà enregistrée. Utilise `/join` pour t'y lier.",
            ephemeral=True,
        )
        return

    rooms_config.add_slot(server_url, slot_name, interaction.user.id, game)
    restart_monitor(server_url)

    await interaction.response.send_message(
        f"✅ Room **{server_url}** ajoutée et liée à ce serveur Discord, avec "
        f"le slot **{slot_name}** ({game}) → {interaction.user.mention}.\n"
        f"Les notifications seront envoyées dans {interaction.channel.mention}.\n"
        f"Les autres joueurs peuvent maintenant utiliser `/join` pour s'y lier.",
        ephemeral=True,
    )


@add_room_cmd.error
async def add_room_cmd_error(interaction: discord.Interaction, error: app_commands.AppCommandError):
    if isinstance(error, app_commands.MissingPermissions):
        await interaction.response.send_message(
            "Il faut la permission **Gérer le serveur** pour ajouter une room.", ephemeral=True
        )
    else:
        raise error


async def server_url_autocomplete(interaction: discord.Interaction, current: str):
    urls = rooms_config.rooms_for_guild(interaction.guild_id)
    return [
        app_commands.Choice(name=u, value=u)
        for u in urls if current.lower() in u.lower()
    ][:25]


@bot.tree.command(name="join", description="Lie un compte Discord à un slot d'une room déjà ajoutée à ce serveur")
@app_commands.describe(
    server_url="La room à rejoindre (voir /rooms ou /add_room d'abord)",
    slot_name="Ton nom de slot exact dans cette room",
    game="Le jeu tel que déclaré dans ton yaml",
    user="Lier ce membre au lieu de toi-même (optionnel)",
)
@app_commands.autocomplete(server_url=server_url_autocomplete)
async def join_cmd(
    interaction: discord.Interaction,
    server_url: str,
    slot_name: str,
    game: str,
    user: discord.Member | None = None,
):
    server_url = "wss://" + server_url
    if server_url not in rooms_config.rooms:
        await interaction.response.send_message(
            f"Room **{server_url}** inconnue. Utilise `/add_room` d'abord, ou vérifie `/rooms`.",
            ephemeral=True,
        )
        return
 
    target = user or interaction.user
    rooms_config.add_slot(server_url, slot_name, target.id, game)
    restart_monitor(server_url)
 
    await interaction.response.send_message(
        f"✅ Slot **{slot_name}** ({game}) lié à {target.mention} sur **{server_url}**.",
        ephemeral=True,
    )


@bot.tree.command(name="set_channel", description="Change le salon de notifications d'une room vers le salon courant")
@app_commands.describe(server_url="La room à modifier (voir /rooms)")
@app_commands.autocomplete(server_url=server_url_autocomplete)
@app_commands.checks.has_permissions(manage_guild=True)
async def set_channel_cmd(interaction: discord.Interaction, server_url: str):
    server_url = "wss://" + server_url
    ok = rooms_config.set_channel_id(server_url, interaction.channel_id)
    if not ok:
        await interaction.response.send_message(f"Room **{server_url}** inconnue.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅ Les notifications de **{server_url}** seront désormais envoyées dans {interaction.channel.mention}.",
        ephemeral=True,
    )


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


@bot.tree.command(name="hints", description="Affiche tous les hints connus concernant un joueur (tableau)")
@app_commands.describe(slot_name="Le nom de slot Archipelago du joueur")
async def hints_cmd(interaction: discord.Interaction, slot_name: str):
    await _send_hints(interaction, slot_name, filter_mode=None)


@bot.tree.command(name="hints_pour", description="Hints des objets DESTINÉS à ce joueur")
@app_commands.describe(slot_name="Le nom de slot Archipelago du joueur")
async def hints_pour_cmd(interaction: discord.Interaction, slot_name: str):
    await _send_hints(interaction, slot_name, filter_mode="pour")


@bot.tree.command(name="hints_chez", description="Hints des objets situés dans le monde de ce joueur")
@app_commands.describe(slot_name="Le nom de slot Archipelago du joueur")
async def hints_chez_cmd(interaction: discord.Interaction, slot_name: str):
    await _send_hints(interaction, slot_name, filter_mode="chez")


async def _send_hints(interaction: discord.Interaction, slot_name: str, filter_mode: str | None):
    server_url = rooms_config.find_server_for_slot(slot_name)
    if server_url is None:
        await interaction.response.send_message(
            f"Aucun slot **{slot_name}** trouvé dans la config.", ephemeral=True
        )
        return

    monitor = active_monitors.get(server_url)
    if monitor is None or not monitor.is_ready():
        await interaction.response.send_message(
            "La surveillance de cette room n'est pas encore prête, réessaie dans quelques secondes.",
            ephemeral=True,
        )
        return

    await interaction.response.defer(thinking=True)

    try:
        hints = await monitor.get_hints_for_slot(slot_name)
    except asyncio.TimeoutError:
        await interaction.followup.send("⏱️ Le serveur Archipelago n'a pas répondu à temps.")
        return

    slot_num = monitor.slot_numbers.get(slot_name.lower())

    if filter_mode == "pour":
        hints = [h for h in hints if monitor.hints_for_slot(h, slot_num)]
        title = f"🎯 Hints pour les objets destinés à **{slot_name}**"
    elif filter_mode == "chez":
        hints = [h for h in hints if monitor.hints_at_slot(h, slot_num)]
        title = f"🗺️ Hints des objets situés chez **{slot_name}**"
    else:
        title = f"📋 Tous les hints concernant **{slot_name}**"

    if not hints:
        await interaction.followup.send(f"{title}\nAucun hint connu pour l'instant.")
        return

    table = monitor.build_hints_table(hints)
    text = f"{title}\n{table}"

    if len(text) > 1900:
        text = text[:1900] + "\n… (liste tronquée)```"
    await interaction.followup.send(text)


async def notify_item_received(server_url: str, receiver_slot: str, item_name: str, sender_name: str):
    channel_id = rooms_config.channel_id_for(server_url)
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", channel_id)
        return

    await channel.send(f"🎁 {receiver_slot} a reçu **{item_name}** (envoyé par {sender_name})")


async def notify_death(server_url: str, slot_name: str, cause: str | None):
    channel_id = rooms_config.channel_id_for(server_url)
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", channel_id)
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
    l'exige) et suit :
      - les items échangés (PrintJSON / ItemSend), diffusés à tous les
        clients connectés quel que soit leur slot
      - les morts DeathLink (Bounced avec tag "DeathLink")
      - les hints, via le mécanisme de stockage serveur (Get/Retrieved sur
        la clé spéciale "_read_hints_{team}_{slot}")
    """

    def __init__(self, server_url: str, config: RoomsConfig):
        self.server_url = server_url
        self.config = config

        self.connect_slot_name = config.any_slot_name(server_url)
        self.connect_game = config.game_for(server_url, self.connect_slot_name) if self.connect_slot_name else None
        self.password = config.password_for(server_url)

        self.ws = None
        # item_id_to_name / location_id_to_name sont indexés par jeu, car les
        # IDs sont spécifiques à chaque jeu dans Archipelago.
        self.item_id_to_name: dict[str, dict[int, str]] = {}
        self.location_id_to_name: dict[str, dict[int, str]] = {}
        self.slot_names: dict[int, str] = {}          # slot number -> slot name
        self.slot_numbers: dict[str, int] = {}         # slot name (lower) -> slot number
        self.my_team: int | None = None

        # requêtes Get/Retrieved en attente, indexées par clé de storage
        self._pending: dict[str, asyncio.Future] = {}

    def is_ready(self) -> bool:
        return self.ws is not None and self.my_team is not None

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
            try:
                async for raw in ws:
                    await self.handle_packet(json.loads(raw))
            finally:
                self.ws = None
                self.my_team = None
                # on annule les requêtes en attente si la connexion tombe
                for fut in self._pending.values():
                    if not fut.done():
                        fut.cancel()
                self._pending.clear()

    async def send(self, packets: list):
        await self.ws.send(json.dumps(packets))

    async def handle_packet(self, packets):
        for pkt in packets:
            cmd = pkt.get("cmd")

            if cmd == "RoomInfo":
                games = self.config.games_for(self.server_url)
                await self.send([{"cmd": "GetDataPackage", "games": games}])

            elif cmd == "DataPackage":
                for game_name, game_data in pkt["data"]["games"].items():
                    self.item_id_to_name[game_name] = {
                        v: k for k, v in game_data["item_name_to_id"].items()
                    }
                    self.location_id_to_name[game_name] = {
                        v: k for k, v in game_data["location_name_to_id"].items()
                    }
                await self.connect_monitor()

            elif cmd == "Connected":
                self.my_team = pkt["team"]
                self.slot_names = {p["slot"]: p["name"] for p in pkt["players"]}
                self.slot_numbers = {name.lower(): num for num, name in self.slot_names.items()}
                log.info("[%s] Surveillance active, %d joueur(s)", self.server_url, len(self.slot_names))

            elif cmd == "ConnectionRefused":
                log.error("[%s] Connexion refusée: %s", self.server_url, pkt.get("errors"))

            elif cmd == "PrintJSON":
                await self.handle_print_json(pkt)

            elif cmd == "Bounced":
                await self.handle_bounce(pkt)

            elif cmd == "Retrieved":
                self.handle_retrieved(pkt)

    async def handle_print_json(self, pkt):
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
        # Les paquets DeathLink sont des Bounced avec "DeathLink" dans les
        # tags, et contiennent data.source = nom du slot qui est mort.
        if "DeathLink" not in pkt.get("tags", []):
            return
        data = pkt.get("data", {})
        source = data.get("source")
        cause = data.get("cause")
        if not source:
            return
        await notify_death(self.server_url, source, cause)

    def handle_retrieved(self, pkt):
        keys = pkt.get("keys", {})
        for key, value in keys.items():
            fut = self._pending.pop(key, None)
            if fut is not None and not fut.done():
                fut.set_result(value)

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

    async def get_hints_for_slot(self, slot_name: str, timeout: float = 10.0) -> list[dict]:
        """Récupère les hints connus impliquant ce slot (qu'il soit le
        destinataire de l'objet ou celui qui doit le trouver), via la clé de
        storage spéciale _read_hints_{team}_{slot}."""
        slot_num = self.slot_numbers.get(slot_name.lower())
        if slot_num is None or self.my_team is None:
            return []

        key = f"_read_hints_{self.my_team}_{slot_num}"

        fut = self._pending.get(key)
        if fut is None or fut.done():
            fut = asyncio.get_event_loop().create_future()
            self._pending[key] = fut
            await self.send([{"cmd": "Get", "keys": [key]}])

        raw_hints = await asyncio.wait_for(fut, timeout=timeout)
        if not raw_hints:
            return []

        result = []
        for h in raw_hints:
            if isinstance(h, dict):
                result.append({
                    "receiving_player": h.get("receiving_player"),
                    "finding_player": h.get("finding_player"),
                    "location": h.get("location"),
                    "item": h.get("item"),
                    "found": h.get("found"),
                    "entrance": h.get("entrance", ""),
                })
            else:
                result.append({
                    "receiving_player": h[0],
                    "finding_player": h[1],
                    "location": h[2],
                    "item": h[3],
                    "found": h[4],
                    "entrance": h[5] if len(h) > 5 else "",
                })
        return result

    def format_hint(self, hint: dict) -> str:
        receiving_name = self.slot_names.get(hint["receiving_player"], f"Slot#{hint['receiving_player']}")
        finding_name = self.slot_names.get(hint["finding_player"], f"Slot#{hint['finding_player']}")

        receiving_game = self.config.game_for(self.server_url, receiving_name)
        finding_game = self.config.game_for(self.server_url, finding_name)

        item_name = self.item_id_to_name.get(receiving_game, {}).get(hint["item"], f"Item#{hint['item']}")
        location_name = self.location_id_to_name.get(finding_game, {}).get(
            hint["location"], f"Location#{hint['location']}"
        )

        status = "✅" if hint["found"] else "❔"
        return f"{status} **{item_name}** (pour {receiving_name}) est à **{location_name}** chez {finding_name}"

    def hints_for_slot(self, hint: dict, slot_num: int) -> bool:
        return hint["receiving_player"] == slot_num

    def hints_at_slot(self, hint: dict, slot_num: int) -> bool:
        return hint["finding_player"] == slot_num

    @staticmethod
    def _truncate(text: str, width: int) -> str:
        if len(text) <= width:
            return text
        return text[: width - 1] + "…"

    def build_hints_table(self, hints: list[dict]) -> str:
        if not hints:
            return "_Aucun hint._"

        col_widths = {"objet": 24, "pour": 14, "chez_loc": 22, "chez_joueur": 12, "trouve": 6}
        rows = []
        for h in hints:
            if not h["found"]:
                receiving_name = self.slot_names.get(h["receiving_player"], f"Slot#{h['receiving_player']}")
                finding_name = self.slot_names.get(h["finding_player"], f"Slot#{h['finding_player']}")
                receiving_game = self.config.game_for(self.server_url, receiving_name)
                finding_game = self.config.game_for(self.server_url, finding_name)
                item_name = self.item_id_to_name.get(receiving_game, {}).get(h["item"], f"Item#{h['item']}")
                location_name = self.location_id_to_name.get(finding_game, {}).get(
                    h["location"], f"Location#{h['location']}"
                )
                rows.append((
                    self._truncate(item_name, col_widths["objet"]),
                    self._truncate(receiving_name, col_widths["pour"]),
                    self._truncate(location_name, col_widths["chez_loc"]),
                    self._truncate(finding_name, col_widths["chez_joueur"]),
                    "✅" if h["found"] else "❔",
                ))

        header = (
            f"{'Objet':<{col_widths['objet']}} "
            f"{'Pour':<{col_widths['pour']}} "
            f"{'Emplacement':<{col_widths['chez_loc']}} "
            f"{'Chez':<{col_widths['chez_joueur']}} "
            f"{'Trouvé':<{col_widths['trouve']}}"
        )
        separator = "-" * len(header)
        lines = [header, separator]
        for objet, pour, loc, chez, trouve in rows:
            lines.append(
                f"{objet:<{col_widths['objet']}} "
                f"{pour:<{col_widths['pour']}} "
                f"{loc:<{col_widths['chez_loc']}} "
                f"{chez:<{col_widths['chez_joueur']}} "
                f"{trouve:<{col_widths['trouve']}}"
            )

        table = "\n".join(lines)
        return f"```\n{table}\n```"


async def archipelago_loop(server_url: str):
    await bot.wait_until_ready()
    monitor = ArchipelagoMonitor(server_url, rooms_config)
    active_monitors[server_url] = monitor
    while not bot.is_closed():
        try:
            await monitor.run()
        except (websockets.ConnectionClosed, OSError) as e:
            log.warning("[%s] Connexion perdue (%s), nouvelle tentative dans 10s...", server_url, e)
            await asyncio.sleep(10)


if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)