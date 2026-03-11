"""Lightweight HTTP health server for K8s probes.

Exposes:
  GET /readyz   -> 200 once ready, 503 before or during shutdown
  GET /healthz  -> 200 always (event loop responsiveness = liveness)
"""

import asyncio
import utils.logger as logger


class HealthServer:
    def __init__(self, port: int = 8080):
        self._port = port
        self._ready = False

    async def start(self) -> None:
        server = await asyncio.start_server(self._handle, "0.0.0.0", self._port)
        asyncio.create_task(server.serve_forever())
        logger.info(f"Health server listening on :{self._port}")

    def mark_ready(self) -> None:
        self._ready = True

    def mark_shutting_down(self) -> None:
        self._ready = False

    async def _handle(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        request_line = await reader.readline()
        while True:
            line = await reader.readline()
            if line == b"\r\n" or line == b"" or line == b"\n":
                break

        path = ""
        if request_line:
            parts = request_line.decode(errors="replace").split()
            if len(parts) >= 2:
                path = parts[1]

        if path == "/readyz":
            ok = self._ready
        elif path == "/healthz":
            ok = True
        else:
            writer.write(b"HTTP/1.1 404 Not Found\r\nContent-Length: 0\r\n\r\n")
            await writer.drain()
            writer.close()
            return

        status = "200 OK" if ok else "503 Service Unavailable"
        body = "ok" if ok else "not ready"
        writer.write(f"HTTP/1.1 {status}\r\nContent-Length: {len(body)}\r\n\r\n{body}".encode())
        await writer.drain()
        writer.close()
