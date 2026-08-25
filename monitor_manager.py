"""
Démarrage, arrêt et boucle de reconnexion des tâches de surveillance
Archipelago — une tâche asyncio par room.
"""

import asyncio

import websockets

from shared import bot, rooms_config, monitor_tasks, active_monitors, log
from archipelago_monitor import ArchipelagoMonitor
from notifications import notify_room_finished

def start_all_monitors():
    for server_url in rooms_config.server_urls():
        room = rooms_config.room_for(server_url)
        if room.get("finish"):
            continue
        if server_url in monitor_tasks and not monitor_tasks[server_url].done():
            continue
        monitor_tasks[server_url] = bot.loop.create_task(archipelago_loop(server_url))

def stop_monitor(server_url: str):
    task = monitor_tasks.get(server_url)

    if task and not task.done():
        task.cancel()

    monitor_tasks.pop(server_url, None)
    active_monitors.pop(server_url, None)

async def finish_room(server_url: str):
    """Marque une room comme terminée et arrête sa surveillance."""

    if not rooms_config.set_finish_state(server_url, 1):
        return False

    stop_monitor(server_url)

    await notify_room_finished(server_url)

    return True

def restart_monitor(server_url: str):
    """Arrête (si besoin) puis relance la tâche de surveillance d'une room."""
    task = monitor_tasks.get(server_url)
    if task and not task.done():
        task.cancel()
    active_monitors.pop(server_url, None)
    monitor_tasks[server_url] = bot.loop.create_task(archipelago_loop(server_url))


async def archipelago_loop(server_url: str):
    await bot.wait_until_ready()
    monitor = ArchipelagoMonitor(server_url, rooms_config, finish_room)
    active_monitors[server_url] = monitor
    while not bot.is_closed():
        try:
            await monitor.run()
        except (websockets.ConnectionClosed, OSError) as e:
            log.warning("[%s] Connexion perdue (%s), nouvelle tentative dans 10s...", server_url, e)
            await asyncio.sleep(10)