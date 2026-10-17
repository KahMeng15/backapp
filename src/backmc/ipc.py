"""Lightweight UNIX domain socket IPC server and client for backmc daemon communication."""

from __future__ import annotations

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any, Callable, Coroutine, Dict, Optional

logger = logging.getLogger("backmc.ipc")

DEFAULT_SOCKET_PATHS = [
    Path("/var/run/backmc/backmc.sock"),
    Path("/tmp/backmc.sock"),
]


def resolve_socket_path(custom_path: Optional[str] = None) -> Path:
    """Find or determine appropriate socket path based on environment or defaults."""
    if custom_path:
        return Path(custom_path)
    env_path = os.environ.get("BACKMC_SOCKET_PATH")
    if env_path:
        return Path(env_path)

    # Prefer /var/run if writable or /var/run/backmc exists, otherwise /tmp
    for p in DEFAULT_SOCKET_PATHS:
        try:
            p.parent.mkdir(parents=True, exist_ok=True)
            return p
        except Exception:
            continue
    return Path("/tmp/backmc.sock")


class IPCServer:
    """Asyncio UNIX domain socket server."""

    def __init__(
        self,
        socket_path: Path,
        handler: Callable[[Dict[str, Any]], Coroutine[Any, Any, Dict[str, Any]]],
    ):
        self.socket_path = socket_path
        self.handler = handler
        self.server: Optional[asyncio.AbstractServer] = None

    async def start(self) -> None:
        """Start listening on the UNIX domain socket."""
        self.socket_path.parent.mkdir(parents=True, exist_ok=True)
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception as e:
                logger.warning("Could not unlink existing socket %s: %s", self.socket_path, e)

        self.server = await asyncio.start_unix_server(
            self._handle_client,
            path=str(self.socket_path),
        )
        # Set permissions so TUI run by same or local group can access
        try:
            os.chmod(self.socket_path, 0o660)
        except Exception:
            pass

        logger.info("IPC server listening on %s", self.socket_path)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        try:
            data = await reader.readline()
            if not data:
                return

            line = data.decode("utf-8").strip()
            if not line:
                return

            try:
                request = json.loads(line)
            except Exception as parse_err:
                response = {"status": "error", "message": f"Malformed JSON: {parse_err}"}
            else:
                try:
                    response = await self.handler(request)
                except Exception as handler_err:
                    response = {"status": "error", "message": f"Handler error: {handler_err}"}

            out_data = json.dumps(response) + "\n"
            writer.write(out_data.encode("utf-8"))
            await writer.drain()
        except Exception as e:
            logger.debug("IPC client interaction error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception:
                pass

    async def stop(self) -> None:
        """Stop and remove the socket file."""
        if self.server:
            self.server.close()
            await self.server.wait_closed()
        if self.socket_path.exists():
            try:
                self.socket_path.unlink()
            except Exception:
                pass
        logger.info("IPC server stopped.")


class IPCClient:
    """Client for querying the backmc daemon via UNIX domain socket."""

    def __init__(self, socket_path: Optional[Path] = None):
        self.socket_path = socket_path or resolve_socket_path()

    def is_daemon_running(self) -> bool:
        """Quick check if daemon socket exists and is responsive."""
        if not self.socket_path.exists():
            return False
        try:
            res = self.send_request({"action": "ping"}, timeout=1.5)
            return res.get("status") == "ok"
        except Exception:
            return False

    def send_request(self, request: Dict[str, Any], timeout: float = 5.0) -> Dict[str, Any]:
        """Synchronously send request over UNIX socket and read JSON response."""
        import socket

        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.settimeout(timeout)
        try:
            sock.connect(str(self.socket_path))
            payload = json.dumps(request) + "\n"
            sock.sendall(payload.encode("utf-8"))

            buf = b""
            while True:
                chunk = sock.recv(4096)
                if not chunk:
                    break
                buf += chunk
                if b"\n" in chunk:
                    break

            if not buf:
                return {"status": "error", "message": "Empty response from daemon"}

            return json.loads(buf.decode("utf-8").strip())
        finally:
            try:
                sock.close()
            except Exception:
                pass
