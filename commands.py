"""
Toutes les commandes slash Discord du bot. Ce module s'importe pour son
effet de bord : il enregistre les commandes sur bot.tree au chargement.
"""

import asyncio

import discord
from discord import app_commands

from shared import bot, rooms_config, active_monitors, log
from monitor_manager import restart_monitor, finish_room

@bot.tree.command(name="reload_rooms", description="Recharge rooms.json et (re)démarre les surveillances manquantes")
async def reload_rooms_cmd(interaction: discord.Interaction):
    from monitor_manager import start_all_monitors
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
            finished_txt = " 🏆" if info.get("finish") else ""
            lines.append(
                f"  • {slot_name} ({info.get('game')}) → <@{info.get('user_id')}>"
                f" — 💀 {info.get('death', 0)}{finished_txt}"
            )
    await interaction.response.send_message("\n".join(lines), ephemeral=True)

@bot.tree.command(name="add_room", description="Ajoute une room Archipelago (avec au moins un slot), liée à ce serveur Discord")
@app_commands.describe(
    port="Adresse de la room, ex: archipelago.gg:12345 (le préfixe wss:// est ajouté automatiquement)",
    slot_name="Ton nom de slot exact dans cette room (le premier joueur lié)",
    game="Le jeu tel que déclaré dans ton yaml",
    password="Mot de passe de la room (laisse vide si aucun)",
)
@app_commands.checks.has_permissions(manage_guild=True)
async def add_room_cmd(
    interaction: discord.Interaction,
    port: str,
    slot_name: str,
    game: str,
    password: str = "",
):
    server_url = "wss://archipelago.gg:" + port

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


@bot.tree.command(name="join", description="Lie un compte Discord à un slot d'une room déjà ajoutée à ce serveur")
@app_commands.describe(
    port="La room à rejoindre (voir /rooms ou /add_room d'abord)",
    slot_name="Ton nom de slot exact dans cette room",
    game="Le jeu tel que déclaré dans ton yaml",
    user="Lier ce membre au lieu de toi-même (optionnel)",
)
async def join_cmd(
    interaction: discord.Interaction,
    port: str,
    slot_name: str,
    game: str,
    user: discord.Member | None = None,
):
    server_url = "wss://archipelago.gg:" + port

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
@app_commands.describe(port="La room à modifier (voir /rooms)")
@app_commands.checks.has_permissions(manage_guild=True)
async def set_channel_cmd(interaction: discord.Interaction, port: str):
    server_url = "wss://archipelago.gg:" + port

    ok = rooms_config.set_channel_id(server_url, interaction.channel_id)
    if not ok:
        await interaction.response.send_message(f"Room **{server_url}** inconnue.", ephemeral=True)
        return
    await interaction.response.send_message(
        f"✅ Les notifications de **{server_url}** seront désormais envoyées dans {interaction.channel.mention}.",
        ephemeral=True,
    )


@bot.tree.command(name="deaths", description="Affiche le compteur de morts DeathLink")
@app_commands.describe(port="La room dont on veut lister les morts")
async def deaths_cmd(interaction: discord.Interaction, port: str):
    server_url = "wss://archipelago.gg:" + port
    room = rooms_config.room_for(server_url)
    if not room:
        await interaction.response.send_message("Aucune room configurée.", ephemeral=True)
        return

    lines = []
    counts = {name: info.get("death", 0) for name, info in rooms_config.slots_for(server_url).items()}
    room_total = rooms_config.total_deaths_for_room(server_url)
    lines.append("**Résumé des morts:**")
    if any(counts.values()):
        for slot_name, count in sorted(counts.items(), key=lambda kv: -kv[1]):
            lines.append(f"  💀 {slot_name} : {count}")
    else:
        lines.append("  _Aucune mort enregistrée pour l'instant_")

    lines.append(f"\n**Total général : {room_total} mort(s)**")
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="reset_deaths", description="Remet à zéro le compteur de morts DeathLink")
async def reset_deaths_cmd(interaction: discord.Interaction):
    rooms_config.reset_deaths()
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

@bot.tree.command(
    name="finish",
    description="Marque une room Archipelago comme terminée"
)
@app_commands.describe(
    port="Le port de la room Archipelago à marquer comme terminée"
)
async def finish_room_cmd(
    interaction: discord.Interaction,
    port: str,
):
    server_url = "wss://archipelago.gg:" + port

    if server_url not in rooms_config.rooms:
        await interaction.response.send_message(
            f"Room **{server_url}** inconnue.",
            ephemeral=True,
        )
        return

    await finish_room(server_url)

    await interaction.response.send_message(
        f"✅ La room **{server_url}** a été arrêtée.",
        ephemeral=True,
    )

@bot.tree.command(
    name="todo_add",
    description="Ajoute un item à la liste TODO d'un joueur"
)
@app_commands.describe(
    player="Le joueur concerné",
    item="L'item à ajouter"
)
async def todo_add_cmd(
    interaction: discord.Interaction,
    player: str,
    item: str,
):
    server_url = rooms_config.find_server_for_slot(player)

    if server_url is None:
        await interaction.response.send_message(
            f"❌ Joueur **{player}** introuvable.",
            ephemeral=True,
        )
        return

    game = rooms_config.game_for(server_url, player)

    if game is None:
        await interaction.response.send_message(
            f"❌ Impossible de trouver le jeu de **{player}**.",
            ephemeral=True,
        )
        return

    monitor = active_monitors.get(server_url)

    if monitor is None:
        await interaction.response.send_message(
            f"❌ La room **{server_url}** n'est pas surveillée.",
            ephemeral=True,
        )
        return

    items = monitor.item_id_to_name.get(game, {})

    matching_item = next(
        (
            name
            for name in items.values()
            if name.lower() == item.lower()
        ),
        None,
    )

    if matching_item is None:
        await interaction.response.send_message(
            f"❌ **{item}** n'existe pas dans le jeu "
            f"de **{player}** (`{game}`).",
            ephemeral=True,
        )
        return

    if not rooms_config.add_todo(
        server_url,
        player,
        matching_item,
    ):
        await interaction.response.send_message(
            f"❌ **{matching_item}** est déjà dans la TODO "
            f"de **{player}**.",
            ephemeral=True,
        )
        return

    await interaction.response.send_message(
        f"✅ **{matching_item}** a été ajouté à la TODO de "
        f"**{player}**.",
    )