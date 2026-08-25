"""
Envoi des différentes notifications Discord : items reçus, morts
DeathLink, victoires (Goal), et résumés de Release/Collect groupés.
"""

from shared import bot, rooms_config, log

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

    new_count = rooms_config.increment_death(server_url, slot_name)
    cause_txt = f" ({cause})" if cause else ""

    await channel.send(f"💀 {mention} est mort{cause_txt} — **{new_count}** mort(s) au compteur")


async def notify_goal(server_url: str, slot_name: str):
    channel_id = rooms_config.channel_id_for(server_url)
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", channel_id)
        return

    user_id = rooms_config.user_id_for(server_url, slot_name)
    mention = f"<@{user_id}>" if user_id else f"**{slot_name}**"
    await channel.send(f"🏆 {mention} a terminé son objectif !")


async def notify_bulk_release(server_url: str, slot_name: str, action: str):
    channel_id = rooms_config.channel_id_for(server_url)
    channel = bot.get_channel(channel_id)
    if channel is None:
        log.warning("Salon de notification introuvable (ID %s)", channel_id)
        return

    await channel.send(f"📦 **{slot_name}** a {action} tous ses items restants d'un coup.")

async def notify_room_finished(server_url: str):
    channel_id = rooms_config.channel_id_for(server_url)
    channel = bot.get_channel(channel_id)

    if channel is None:
        log.warning(
            "Salon de notification introuvable (ID %s)",
            channel_id,
        )
        return

    death_count = death_final_count(server_url)

    await channel.send(
        f"🏁 **La room est terminée !**\n\n"
        f"{death_count}"
    )

def death_final_count(server_url: str) -> str:
	lines = []

	counts = {
		name: info.get("death", 0)
		for name, info in rooms_config.slots_for(server_url).items()
	}

	room_total = rooms_config.total_deaths_for_room(server_url)

	lines.append("**Décompte des morts :**")

	for slot_name, count in sorted(
		counts.items(),
		key=lambda kv: -kv[1]
	):
		lines.append(
			f"  💀 {slot_name} : {count}"
		)

	lines.append(
		f"\n**Total : {room_total} mort(s)**"
	)

	return "\n".join(lines)