"""
Client Archipelago : une instance par room. Maintient la connexion
WebSocket, traduit les paquets du protocole (items, morts DeathLink,
victoires, hints) et déclenche les notifications correspondantes.
"""

import asyncio
import json
import ssl

import websockets

from shared import log
from rooms_config import RoomsConfig
from notifications import (
    notify_item_received,
    notify_todo_item_received,
    notify_death,
    notify_goal,
    notify_bulk_release,
    notify_room_complete,
)


class ArchipelagoMonitor:
    """
    Se connecte à une room Archipelago avec un slot réel (le protocole AP
    l'exige) et suit :
      - les items échangés (PrintJSON / ItemSend), diffusés à tous les
        clients connectés quel que soit leur slot — coupés pour un
        destinataire une fois que celui-ci a atteint son Goal
      - les victoires (PrintJSON / Goal), persistées dans rooms.json
        (champ "finish" du slot)
      - les release/collect groupés (PrintJSON / Release, Collect)
      - les morts DeathLink (Bounced avec tag "DeathLink"), persistées
        dans rooms.json (champ "death" du slot)
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
        self.item_id_to_name: dict[str, dict[int, str]] = {}
        self.location_id_to_name: dict[str, dict[int, str]] = {}
        self.slot_names: dict[int, str] = {}
        self.slot_numbers: dict[str, int] = {}
        self.my_team: int | None = None

        # numéros de slot ayant déjà atteint leur Goal, reconstruit depuis
        # rooms.json à chaque connexion (une fois qu'on connaît slot_numbers)
        self.finished_slot_nums: set[int] = set()

        # True dès que TOUS les slots de la room ont terminé : empêche toute
        # reconnexion ultérieure de cette room (lu par monitor_manager).
        self.should_stop: bool = False

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
                # Reconstruit l'ensemble des slots déjà terminés à partir de
                # rooms.json, maintenant qu'on a la correspondance nom<->numéro.
                self.finished_slot_nums = {
                    num for name, num in self.slot_numbers.items()
                    if self.config.is_finished(self.server_url, name)
                }
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
        pkt_type = pkt.get("type")

        if pkt_type == "Goal":
            await self.handle_goal(pkt)
            return

        if pkt_type in ("Release", "Collect"):
            await self.handle_bulk_release(pkt)
            return

        if pkt_type != "ItemSend":
            return

        item_data = pkt.get("item", {})
        receiving_slot = pkt.get("receiving")
        sender_slot = item_data.get("player")

        # Un joueur qui a déjà terminé son objectif ne doit plus générer de
        # notification d'item reçu (typiquement les rafales post-Goal /
        # post-Release).
        if receiving_slot in self.finished_slot_nums:
            return

        receiver_name = self.slot_names.get(receiving_slot, f"Slot#{receiving_slot}")
        sender_name = self.slot_names.get(sender_slot, f"Slot#{sender_slot}")

        receiver_game = self.config.game_for(self.server_url, receiver_name)
        game_items = self.item_id_to_name.get(receiver_game, {})
        item_name = game_items.get(item_data.get("item"), f"Item#{item_data.get('item')}")

        if self.config.is_todo(self.server_url, receiver_name, item_name):
            self.config.remove_todo(self.server_url, receiver_name, item_name)
            await notify_todo_item_received(self.server_url, receiver_name, item_name, sender_name)
        else:
            await notify_item_received(self.server_url, receiver_name, item_name, sender_name)

    async def handle_goal(self, pkt):
        slot_num = pkt.get("slot")
        if slot_num is None:
            return

        slot_name = self.slot_names.get(slot_num, f"Slot#{slot_num}")

        # Évite de traiter deux fois le même Goal (doublon serveur,
        # reconnexion, etc.)
        if slot_num in self.finished_slot_nums:
            return

        self.finished_slot_nums.add(slot_num)
        self.config.mark_finished(self.server_url, slot_name)

        await notify_goal(self.server_url, slot_name)

        # Seulement si TOUT LE MONDE a terminé : on marque la room comme
        # terminée, on envoie le décompte des morts, et on ferme la
        # connexion pour de bon.
        if self.config.all_finished(self.server_url):
            log.info("[%s] Tous les joueurs ont terminé, arrêt de la surveillance.", self.server_url)
            self.config.mark_room_finished(self.server_url)
            self.should_stop = True
            await notify_room_complete(self.server_url)
            if self.ws is not None:
                await self.ws.close()

    async def handle_bulk_release(self, pkt):
        data = pkt.get("data", [])
        slot_num = None
        for part in data:
            if isinstance(part, dict) and part.get("type") == "player_id":
                slot_num = part.get("text")
                break
        slot_name = self.slot_names.get(int(slot_num), f"Slot#{slot_num}") if slot_num else "Un joueur"
        action = "relâché" if pkt.get("type") == "Release" else "récupéré"
        await notify_bulk_release(self.server_url, slot_name, action)

    async def handle_bounce(self, pkt):
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