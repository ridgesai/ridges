"""Minimal HTTP health endpoint for Kubernetes liveness and readiness probes.

Usage (in validator/main.py):

    import validator.healthz as healthz
    asyncio.create_task(healthz.serve(get_session_id=lambda: session_id))

Pod spec:

    livenessProbe:
      httpGet: {path: /healthz, port: 8080}
      initialDelaySeconds: 30
      periodSeconds: 15
    readinessProbe:
      httpGet: {path: /healthz, port: 8080}
      initialDelaySeconds: 10
      periodSeconds: 10
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import utils.logger as logger


async def serve(
    *,
    get_session_id: Callable[[], Any],
    host: str = "0.0.0.0",
    port: int = 8080,
) -> None:
    """Start the aiohttp health server.  Runs forever; call from a background task."""
    try:
        from aiohttp import web
    except ImportError:
        logger.warning("aiohttp not installed – /healthz endpoint disabled (install aiohttp to enable)")
        return

    async def healthz(request: web.Request) -> web.Response:
        if get_session_id() is not None:
            return web.Response(text="ok")
        return web.Response(status=503, text="not registered")

    app = web.Application()
    app.router.add_get("/healthz", healthz)

    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, host, port)
    await site.start()
    logger.info(f"Health endpoint listening on {host}:{port}/healthz")

    # Keep running until cancelled
    import asyncio

    try:
        while True:
            await asyncio.sleep(3600)
    finally:
        await runner.cleanup()
