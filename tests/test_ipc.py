"""Tests for daemon IPC server and client interactions."""

import asyncio
from pathlib import Path
import pytest
from backmc.daemon import BackupDaemon
from backmc.ipc import IPCClient


@pytest.mark.asyncio
async def test_ipc_communication(tmp_path: Path):
    jobs_dir = tmp_path / "jobs"
    jobs_dir.mkdir()
    
    # Create sample job
    (jobs_dir / "sample.yaml").write_text("""
name: "sample"
schedule: "0 0 * * *"
format: "tar.gz"
source_dir: "/tmp"
""")

    import uuid
    sock_path = Path(f"/tmp/bmc_{uuid.uuid4().hex[:8]}.sock")
    daemon = BackupDaemon(jobs_dir=jobs_dir, socket_path=sock_path, dry_run=True)

    daemon.load_configurations()
    await daemon.ipc_server_start() if hasattr(daemon, "ipc_server_start") else None
    
    from backmc.ipc import IPCServer
    server = IPCServer(sock_path, daemon._handle_ipc_request)
    await server.start()

    try:
        client = IPCClient(sock_path)
        loop = asyncio.get_running_loop()
        is_running = await loop.run_in_executor(None, client.is_daemon_running)
        assert is_running is True

        # Test ping
        res_ping = await loop.run_in_executor(None, client.send_request, {"action": "ping"})
        assert res_ping.get("status") == "ok"
        assert res_ping.get("message") == "pong"

        # Test status
        res_status = await loop.run_in_executor(None, client.send_request, {"action": "status"})
        assert res_status.get("status") == "ok"
        assert "sample" in res_status.get("data", {}).get("jobs", {})

        # Test reload
        res_reload = await loop.run_in_executor(None, client.send_request, {"action": "reload"})
        assert res_reload.get("status") == "ok"

    finally:
        await server.stop()
