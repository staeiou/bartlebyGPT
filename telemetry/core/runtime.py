from __future__ import annotations

import asyncio
import logging
import signal
import subprocess
import time

from .deployment import Deployment, SourceContext
from .history import battery_history_loop
from .http import make_server
from .reading import Snapshot

log = logging.getLogger("telemetry")


def _reset_bluetooth_stack(log) -> None:
    log.warning("bluetooth: restarting bluetooth.service after repeated BLE source failures")
    try:
        subprocess.run(
            ["systemctl", "restart", "bluetooth"],
            check=True,
            capture_output=True,
            text=True,
            timeout=20,
        )
        log.warning("bluetooth: service restarted; waiting for adapter to reinitialize")
        time.sleep(6)
    except (OSError, subprocess.SubprocessError) as err:
        log.error("bluetooth: reset failed: %s", err)


async def supervise(source, ctx: SourceContext) -> None:
    consecutive_failures = 0
    while True:
        try:
            await source.run(ctx)
            consecutive_failures = 0
            ctx.log.warning("source %s returned; restarting", source.kind)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            consecutive_failures += 1
            ctx.log.warning("source %s failed: %s", source.kind, err)
            if (
                source.reset_after_failures > 0
                and consecutive_failures >= source.reset_after_failures
            ):
                await asyncio.to_thread(_reset_bluetooth_stack, ctx.log)
                consecutive_failures = 0
        await asyncio.sleep(source.reconnect_delay)


async def run(deployment: Deployment) -> None:
    snapshot = Snapshot()
    ctx = SourceContext(emit=snapshot.update, log=log)

    server = make_server(deployment.http.host, deployment.http.port, deployment, snapshot)
    loop = asyncio.get_running_loop()
    server_task = loop.run_in_executor(None, server.serve_forever)
    log.info(
        "telemetry[%s]: HTTP on %s:%s, %d source(s)",
        deployment.id,
        deployment.http.host,
        deployment.http.port,
        len(deployment.sources),
    )

    tasks = [asyncio.create_task(supervise(source, ctx), name=f"source:{source.id}") for source in deployment.sources]
    tasks.append(asyncio.create_task(battery_history_loop(deployment, snapshot, log), name="battery-history"))
    stop = asyncio.Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            loop.add_signal_handler(sig, stop.set)
        except NotImplementedError:
            pass

    try:
        await stop.wait()
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        server.shutdown()
        await server_task
        server.server_close()
        log.info("telemetry[%s]: stopped", deployment.id)
