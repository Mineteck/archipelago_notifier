"""
Configuration des rooms Archipelago : chargement, sauvegarde et toutes les
opérations de lecture/écriture sur rooms.json (slots, morts, victoires,
salon de notification, etc).
"""

import json
import logging
from pathlib import Path

log = logging.getLogger("ap-discord-bot")


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

	def room_for(self, server_url: str) -> dict:
		return self.rooms.get(server_url, {})

	def user_id_for(self, server_url: str, slot_name: str) -> int | None:
		entry = self.slots_for(server_url).get(slot_name)
		return entry.get("user_id") if entry else None

	def game_for(self, server_url: str, slot_name: str) -> str | None:
		entry = self.slots_for(server_url).get(slot_name)
		return entry.get("game") if entry else None

	def any_slot_name(self, server_url: str) -> str | None:
		slots = self.slots_for(server_url)
		return next(iter(slots), None)

	def games_for(self, server_url: str) -> list[str]:
		return sorted({v["game"] for v in self.slots_for(server_url).values()})

	def find_server_for_slot(self, slot_name: str) -> str | None:
		target = slot_name.lower()
		for server_url in self.server_urls():
			for name in self.slots_for(server_url):
				if name.lower() == target:
					return server_url
		return None

	def _find_slot_key(self, server_url: str, slot_name: str) -> str | None:
		"""Retrouve la clé exacte (casse d'origine) d'un slot, insensible à la casse."""
		target = slot_name.lower()
		for name in self.slots_for(server_url):
			if name.lower() == target:
				return name
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

	def set_finish_state(self, server_url: str, state: int) -> bool:
		if server_url not in self.rooms:
			return False
		self.rooms[server_url]["finish"] = state
		self.save()
		return True
			

	def rooms_for_guild(self, guild_id: int | None) -> list[str]:
		return [url for url, data in self.rooms.items() if data.get("guild_id") == guild_id]

	def add_room(self, server_url: str, password: str, guild_id: int, channel_id: int) -> bool:
		if server_url in self.rooms:
			return False
		self.rooms[server_url] = {
			"guild_id": guild_id,
			"channel_id": channel_id,
			"password": password,
			"finish": 0,
			"slot": {},
		}
		self.save()
		return True

	def add_slot(self, server_url: str, slot_name: str, user_id: int, game: str) -> bool:
		"""Ajoute ou met à jour un slot. Préserve death/finish si le slot
		existait déjà (rejoindre ne doit pas remettre les compteurs à zéro)."""
		if server_url not in self.rooms:
			return False
		slots = self.rooms[server_url].setdefault("slot", {})
		key = self._find_slot_key(server_url, slot_name) or slot_name
		existing = slots.get(key, {})
		slots[key] = {
			"user_id": user_id,
			"game": game,
			"death": existing.get("death", 0),
			"finish": existing.get("finish", 0),
		}
		self.save()
		return True

	# -- Victoire (Goal) --------------------------------------------------

	def all_finished(self, server_url: str) -> bool:
		slots = self.slots_for(server_url)

		if not slots:
			return False

		return all(
			info.get("finish", 0) == 1
			for info in slots.values()
		)

	def is_finished(self, server_url: str, slot_name: str) -> bool:
		key = self._find_slot_key(server_url, slot_name)
		if key is None:
			return False
		return bool(self.rooms[server_url]["slot"][key].get("finish", 0))

	def mark_finished(self, server_url: str, slot_name: str):
		key = self._find_slot_key(server_url, slot_name)
		if key is None:
			return
		self.rooms[server_url]["slot"][key]["finish"] = 1
		self.save()

	# -- Compteur de morts DeathLink --------------------------------------

	def death_count_for(self, server_url: str, slot_name: str) -> int:
		key = self._find_slot_key(server_url, slot_name)
		if key is None:
			return 0
		return self.rooms[server_url]["slot"][key].get("death", 0)

	def increment_death(self, server_url: str, slot_name: str) -> int:
		key = self._find_slot_key(server_url, slot_name)
		if key is None:
			return 0
		slot = self.rooms[server_url]["slot"][key]
		slot["death"] = slot.get("death", 0) + 1
		self.save()
		return slot["death"]

	def total_deaths_for_room(self, server_url: str) -> int:
		return sum(v.get("death", 0) for v in self.slots_for(server_url).values())

	def total_deaths_all(self) -> int:
		return sum(self.total_deaths_for_room(u) for u in self.server_urls())

	def reset_deaths(self, server_url: str | None = None):
		targets = [server_url] if server_url else self.server_urls()
		for url in targets:
			for slot in self.rooms.get(url, {}).get("slot", {}).values():
				slot["death"] = 0
		self.save()