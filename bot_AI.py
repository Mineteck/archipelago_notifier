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

# ---------------------------------------------------------------------------
# CONFIG - à adapter
# ---------------------------------------------------------------------------

DISCORD_BOT_TOKEN = "xxx"
NOTIFY_CHANNEL_ID = 965590122529689670      # ID du salon où poster les notifs

AP_SERVER = "wss://archipelago.gg:62811"    # adresse:port de la room AP
AP_GAME = "Baldur's Gate 3"                          # nom du jeu tel que dans le yaml
# Multiworld: pas besoin d'un slot précis pour écouter tout le monde,
# on se connecte en spectateur/texte pour voir passer tous les items.
AP_MONITOR_SLOT = "Portier"                      # laisse None -> connexion "TextOnly" / spectateur si supporté par la room

AP_MONITOR_PASSWORD = ""    
LINKS_FILE = Path("links.json")             # discord_user_id -> slot_name (et inverse)
 
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ap-discord-bot")
 
# ---------------------------------------------------------------------------
# Stockage des liaisons utilisateur <-> slot
# ---------------------------------------------------------------------------
 
 
class LinkStore:
    def __init__(self, path: Path):
        self.path = path
        self.data: dict[str, int] = {}  # slot_name (lower) -> discord_user_id
        self.load()
 
    def load(self):
        if self.path.exists():
            self.data = json.loads(self.path.read_text())
 
    def save(self):
        self.path.write_text(json.dumps(self.data, indent=2))
 
    def link(self, slot_name: str, discord_id: int):
        self.data[slot_name.lower()] = discord_id
        self.save()
 
    def unlink(self, slot_name: str) -> bool:
        removed = self.data.pop(slot_name.lower(), None)
        if removed is not None:
            self.save()
            return True
        return False
 
    def discord_id_for_slot(self, slot_name: str) -> int | None:
        return self.data.get(slot_name.lower())
 
    def all(self) -> dict[str, int]:
        return dict(self.data)
 
 
links = LinkStore(LINKS_FILE)
 
# ---------------------------------------------------------------------------
# Bot Discord
# ---------------------------------------------------------------------------
 
intents = discord.Intents.default()
bot = commands.Bot(command_prefix="!", intents=intents)
 
 
@bot.event
async def on_ready():
    log.info("Bot connecté en tant que %s", bot.user)
    await bot.tree.sync()
    bot.loop.create_task(archipelago_loop())
 
 
@bot.tree.command(name="link", description="Lie ton compte Discord à ton nom de slot Archipelago")
@app_commands.describe(slot_name="Ton nom de slot exact dans la room Archipelago")
async def link_cmd(interaction: discord.Interaction, slot_name: str):
    links.link(slot_name, interaction.user.id)
    await interaction.response.send_message(
        f"✅ Slot **{slot_name}** lié à {interaction.user.mention}. "
        f"Tu seras pingé ici quand ce slot recevra un item.",
        ephemeral=True,
    )
 
 
@bot.tree.command(name="unlink", description="Supprime la liaison pour un slot Archipelago")
@app_commands.describe(slot_name="Le nom de slot à délier")
async def unlink_cmd(interaction: discord.Interaction, slot_name: str):
    ok = links.unlink(slot_name)
    if ok:
        await interaction.response.send_message(f"🗑️ Liaison supprimée pour **{slot_name}**.", ephemeral=True)
    else:
        await interaction.response.send_message(f"Aucune liaison trouvée pour **{slot_name}**.", ephemeral=True)
 
 
@bot.tree.command(name="links", description="Liste les liaisons slot <-> utilisateur connues")
async def links_cmd(interaction: discord.Interaction):
    all_links = links.all()
    if not all_links:
        await interaction.response.send_message("Aucune liaison enregistrée pour le moment.", ephemeral=True)
        return
    lines = [f"**{slot}** → <@{uid}>" for slot, uid in all_links.items()]
    await interaction.response.send_message("\n".join(lines), ephemeral=True)
 
 
async def notify_item_received(slot_name: str, item_name: str, sender_name: str, receiver_name: str):
    channel = bot.get_channel(NOTIFY_CHANNEL_ID)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", NOTIFY_CHANNEL_ID)
        return
 
    discord_id = links.discord_id_for_slot(slot_name)
    mention = f"<@{discord_id}>" if discord_id else f"**{slot_name}**"
 
    await channel.send(f"🎁 {mention} a reçu **{item_name}** (envoyé par {sender_name})")
 
 
# ---------------------------------------------------------------------------
# Client Archipelago (tâche de fond)
# ---------------------------------------------------------------------------
 
 
class ArchipelagoMonitor:
    """
    Se connecte au serveur AP et suit TOUS les échanges d'items de la room
    (pas seulement ceux d'un slot précis), en écoutant les messages
    PrintJSON de type 'ItemSend', qui contiennent l'expéditeur et le
    destinataire pour chaque item envoyé dans le multiworld.
    """
 
    def __init__(self, server: str, game: str, slot_name: str, password: str = ""):
        self.server = server
        self.game = game
        self.slot_name = slot_name
        self.password = password
        self.ws = None
        self.item_id_to_name = {}
        self.slot_names = {}  # slot number -> slot name
 
    async def run(self):
        ssl_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        ssl_ctx.check_hostname = False
        ssl_ctx.verify_mode = ssl.CERT_NONE
 
        async with websockets.connect(
            self.server, ssl=ssl_ctx if self.server.startswith("wss") else None
        ) as ws:
            self.ws = ws
            log.info("Connecté au serveur Archipelago %s", self.server)
            async for raw in ws:
                await self.handle_packet(json.loads(raw))
 
    async def send(self, packets: list):
        await self.ws.send(json.dumps(packets))
 
    async def handle_packet(self, packets):
        for pkt in packets:
            cmd = pkt.get("cmd")
 
            if cmd == "RoomInfo":
                await self.send([{"cmd": "GetDataPackage"}])
 
            elif cmd == "DataPackage":
                games = pkt["data"]["games"]
                if self.game in games:
                    self.item_id_to_name = {
                        v: k for k, v in games[self.game]["item_name_to_id"].items()
                    }
                await self.connect_spectator()
 
            elif cmd == "Connected":
                self.slot_names = {p["slot"]: p["name"] for p in pkt["players"]}
                log.info("Moniteur connecté, %d joueurs dans la room", len(self.slot_names))
 
            elif cmd == "ConnectionRefused":
                log.error("Connexion refusée: %s", pkt.get("errors"))
 
            elif cmd == "PrintJSON":
                await self.handle_print_json(pkt)
 
    async def handle_print_json(self, pkt):
        if pkt.get("type") != "ItemSend":
            return
        item_data = pkt.get("item", {})
        receiving_slot = pkt.get("receiving")
        sender_slot = item_data.get("player")
 
        item_name = self.item_id_to_name.get(item_data.get("item"), f"Item#{item_data.get('item')}")
        receiver_name = self.slot_names.get(receiving_slot, f"Slot#{receiving_slot}")
        sender_name = self.slot_names.get(sender_slot, f"Slot#{sender_slot}")
 
        await notify_item_received(receiver_name, item_name, sender_name, receiver_name)
 
    async def connect_spectator(self):
        # On se connecte avec un slot réel existant (obligatoire côté
        # protocole AP). Le tag "TextOnly" évite que le serveur nous traite
        # comme un vrai client de jeu. On voit quand même passer TOUS les
        # ItemSend de la room, pas seulement ceux de ce slot.
        await self.send([{
            "cmd": "Connect",
            "game": self.game,
            "name": self.slot_name,
            "password": self.password or None,
            "uuid": "ap-discord-bot-monitor",
            "version": {"major": 0, "minor": 5, "build": 0, "class": "Version"},
            "items_handling": 0,
            "tags": ["TextOnly"],
            "slot_data": False,
        }])
 
 
async def archipelago_loop():
    await bot.wait_until_ready()
    monitor = ArchipelagoMonitor(AP_SERVER, AP_GAME, AP_MONITOR_SLOT, AP_MONITOR_PASSWORD)
    while not bot.is_closed():
        try:
            await monitor.run()
        except (websockets.ConnectionClosed, OSError) as e:
            log.warning("Connexion Archipelago perdue (%s), nouvelle tentative dans 10s...", e)
            await asyncio.sleep(10)
 
 
if __name__ == "__main__":
    bot.run(DISCORD_BOT_TOKEN)