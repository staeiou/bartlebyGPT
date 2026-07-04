from __future__ import annotations

import asyncio
import logging
import signal

from .deployment import Deployment, SourceContext
from .http import make_server
from .reading import Snapshot

log = logging.getLogger("telemetry")


async def supervise(source, ctx: SourceContext) -> None:
    while True:
        try:
            await source.run(ctx)
            ctx.log.warning("source %s returned; restarting", source.kind)
        except asyncio.CancelledError:
            raise
        except Exception as err:
            ctx.log.warning("source %s failed: %s", source.kind, err)
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
