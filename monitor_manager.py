"""
Démarrage, arrêt et boucle de reconnexion des tâches de surveillance
Archipelago — une tâche asyncio par room.
"""

import asyncio

import websockets

from shared import bot, rooms_config, monitor_tasks, active_monitors, log
from archipelago_monitor import ArchipelagoMonitor


def start_all_monitors():
    for server_url in rooms_config.server_urls():
        if rooms_config.is_room_finished(server_url):
            continue
        if server_url in monitor_tasks and not monitor_tasks[server_url].done():
            continue
        monitor_tasks[server_url] = bot.loop.create_task(archipelago_loop(server_url))


def restart_monitor(server_url: str):
    """Arrête (si besoin) puis relance la tâche de surveillance d'une room."""
    task = monitor_tasks.get(server_url)
    if task and not task.done():
        task.cancel()
    active_monitors.pop(server_url, None)
    monitor_tasks[server_url] = bot.loop.create_task(archipelago_loop(server_url))


def stop_monitor(server_url: str):
    """Arrête définitivement la surveillance d'une room, sans la relancer.
    Utilisé par /finish_room et par l'arrêt automatique une fois la room
    marquée terminée."""
    task = monitor_tasks.pop(server_url, None)
    if task and not task.done():
        task.cancel()
    active_monitors.pop(server_url, None)


async def archipelago_loop(server_url: str):
    await bot.wait_until_ready()

    if rooms_config.is_room_finished(server_url):
        log.info("[%s] Room déjà marquée terminée, surveillance non démarrée.", server_url)
        return

    monitor = ArchipelagoMonitor(server_url, rooms_config)
    active_monitors[server_url] = monitor

    while not bot.is_closed():
        if monitor.should_stop or rooms_config.is_room_finished(server_url):
            log.info("[%s] Room terminée, arrêt définitif de la surveillance.", server_url)
            break

        try:
            await monitor.run()
        except (websockets.ConnectionClosed, OSError) as e:
            if monitor.should_stop or rooms_config.is_room_finished(server_url):
                log.info("[%s] Room terminée, arrêt définitif de la surveillance.", server_url)
                break
            log.warning("[%s] Connexion perdue (%s), nouvelle tentative dans 10s...", server_url, e)
            await asyncio.sleep(10)
        else:
            # run() est revenu sans exception (fermeture propre) : petite
            # pause pour éviter une boucle de reconnexion trop agressive,
            # sauf si on s'arrête volontairement.
            if not monitor.should_stop:
                await asyncio.sleep(5)

    active_monitors.pop(server_url, None)