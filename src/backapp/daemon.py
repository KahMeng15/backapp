"""Zero-impact backup daemon scheduler and background service."""

from __future__ import annotations

import asyncio
import datetime
import logging
import os
import signal
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from croniter import croniter

from backapp.config import ConfigError, JobConfig, load_jobs_from_directory, seed_examples_if_empty
from backapp.engine import BackupEngine, BackupResult, format_bytes, get_restore_command
from backapp.ipc import IPCServer, resolve_socket_path
from backapp.retention import apply_retention, list_job_backups
from backapp.state import DaemonState

logger = logging.getLogger("backapp.daemon")


class BackupDaemon:
    """Production-ready lightweight backup daemon using croniter and asyncio."""

    def __init__(
        self,
        jobs_dir: Path = Path("/etc/backup/jobs"),
        socket_path: Optional[Path] = None,
        dry_run: bool = False,
    ):
        self.jobs_dir = jobs_dir
        self.socket_path = socket_path or resolve_socket_path()
        self.dry_run = dry_run
        
        self.jobs: Dict[str, JobConfig] = {}
        self.next_runs: Dict[str, datetime.datetime] = {}
        self.state = DaemonState()
        self.engine = BackupEngine(dry_run=dry_run)
        
        self.running: bool = False
        self.ipc_server: Optional[IPCServer] = None
        self._wake_event = asyncio.Event()
        self._running_jobs: set[str] = set()

        # Connect log handler to root logger
        logging.getLogger().addHandler(self.state.log_handler)

    def load_configurations(self) -> int:
        """Load or reload all job configuration files from the jobs directory."""
        logger.info("Loading backup job configurations from %s", self.jobs_dir)
        try:
            new_jobs = load_jobs_from_directory(self.jobs_dir)
        except Exception as e:
            logger.error("Failed to load jobs from %s: %s", self.jobs_dir, e)
            return len(self.jobs)

        # Auto-seed template configs if /etc/backup/examples or BACKUP_EXAMPLES_DIR is empty
        examples_dir = Path(os.environ.get("BACKUP_EXAMPLES_DIR", "/etc/backup/examples"))
        seeded = seed_examples_if_empty(examples_dir)
        if seeded:
            logger.info("Auto-seeded %d template config(s) into %s: %s", len(seeded), examples_dir, ", ".join(seeded))

        now = datetime.datetime.now()
        self.jobs = new_jobs
        self.next_runs.clear()

        if not self.jobs:
            logger.warning(
                "No backup jobs found in %s! "
                "Copy starter templates from %s or run 'backapp init' to define backup jobs.",
                self.jobs_dir,
                examples_dir,
            )

        for name, job in self.jobs.items():
            self.state.register_job(
                name=name,
                job_type=job.type,
                schedule=job.schedule,
                keep_last=job.retention.keep_last,
            )

            if job.schedule:
                try:
                    itr = croniter(job.schedule, now)
                    next_dt = itr.get_next(datetime.datetime)
                    self.next_runs[name] = next_dt
                    self.state.set_next_run(name, next_dt)
                    logger.info("Job '%s' scheduled. Next run at %s", name, next_dt)
                except Exception as e:
                    logger.error("Failed to compute next schedule for '%s': %s", name, e)
                    self.state.set_next_run(name, None)
            else:
                self.state.set_next_run(name, None)
                logger.info("Job '%s' loaded for manual/on-demand execution only.", name)

        logger.info("Successfully loaded %d backup jobs.", len(self.jobs))
        return len(self.jobs)

    async def _handle_ipc_request(self, req: Dict[str, Any]) -> Dict[str, Any]:
        """Handle incoming IPC requests from TUI or CLI."""
        action = req.get("action")

        if action == "ping":
            return {"status": "ok", "message": "pong"}

        elif action == "status":
            import resource
            rss_kb = (
                resource.getrusage(resource.RUSAGE_SELF).ru_maxrss // 1024
                if sys.platform == "darwin"
                else resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            )
            rss_mb = round(rss_kb / 1024.0, 2)
            
            summary = self.state.get_summary()
            return {
                "status": "ok",
                "daemon": {
                    "pid": os.getpid(),
                    "rss_mb": rss_mb,
                    "running_jobs": list(self._running_jobs),
                    "jobs_dir": str(self.jobs_dir),
                    "dry_run": self.dry_run,
                },
                "data": summary,
            }

        elif action in ("trigger", "trigger_all"):
            job_name = req.get("job_name")
            if action == "trigger_all" or job_name in ("all", "*", None):
                triggered = []
                for name in list(self.jobs.keys()):
                    if name not in self._running_jobs:
                        asyncio.create_task(self.run_job_async(name, manual=True))
                        triggered.append(name)
                return {"status": "ok", "message": f"Triggered {len(triggered)} backup job(s): {', '.join(triggered)}"}

            if not job_name or job_name not in self.jobs:
                return {"status": "error", "message": f"Job '{job_name}' not found."}
            
            if job_name in self._running_jobs:
                return {"status": "error", "message": f"Job '{job_name}' is already running."}

            asyncio.create_task(self.run_job_async(job_name, manual=True))
            return {"status": "ok", "message": f"Triggered backup for job '{job_name}'."}

        elif action == "reload":
            count = self.load_configurations()
            self._wake_event.set()
            return {"status": "ok", "message": f"Reloaded {count} job configurations."}

        elif action == "list_backups":
            job_name = req.get("job_name")
            if not job_name or job_name not in self.jobs:
                return {"status": "error", "message": f"Job '{job_name}' not found."}

            job = self.jobs[job_name]
            files = list_job_backups(job)
            file_list = []
            for f in files:
                stat = f.stat()
                file_list.append({
                    "filename": f.name,
                    "path": str(f),
                    "size_bytes": stat.st_size,
                    "size_formatted": format_bytes(stat.st_size),
                    "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "restore_command": get_restore_command(f),
                })
            return {"status": "ok", "job_name": job_name, "backups": file_list}

        elif action == "logs":
            limit = int(req.get("limit", 100))
            logs = self.state.log_handler.get_logs(limit=limit)
            return {"status": "ok", "logs": logs}

        return {"status": "error", "message": f"Unknown action '{action}'"}

    async def run_job_async(self, job_name: str, manual: bool = False) -> BackupResult:
        """Execute a backup job in a background worker thread."""
        job = self.jobs[job_name]
        self._running_jobs.add(job_name)
        self.state.set_job_running(job_name)

        logger.info(
            "Triggering backup job '%s' (type=%s, manual=%s)...",
            job.name,
            job.type,
            manual,
        )

        loop = asyncio.get_running_loop()
        try:
            result: BackupResult = await loop.run_in_executor(
                None, self.engine.execute_job, job
            )
            
            # Post-execution retention pruning
            if result.success and not self.dry_run:
                try:
                    pruned = await loop.run_in_executor(None, apply_retention, job)
                    if pruned:
                        logger.info("Retention pruned %d old backup(s) for job '%s'.", len(pruned), job.name)
                except Exception as ret_err:
                    logger.error("Retention error for '%s': %s", job.name, ret_err)

            self.state.set_job_completed(
                name=job.name,
                success=result.success,
                duration=result.duration_seconds,
                timestamp=result.timestamp,
                backup_file=result.output_file if result.success else None,
                backup_size=result.file_size_bytes if result.success else None,
                error_msg=result.error_message,
            )

            if job.schedule:
                try:
                    itr = croniter(job.schedule, datetime.datetime.now())
                    next_dt = itr.get_next(datetime.datetime)
                    self.next_runs[job.name] = next_dt
                    self.state.set_next_run(job.name, next_dt)
                except Exception:
                    pass

            return result
        finally:
            self._running_jobs.discard(job_name)

    async def start(self) -> None:
        """Run the main daemon scheduler loop."""
        self.running = True
        self.load_configurations()

        # Start IPC socket server
        self.ipc_server = IPCServer(self.socket_path, self._handle_ipc_request)
        await self.ipc_server.start()

        # Setup OS signal handlers
        loop = asyncio.get_running_loop()
        for sig in (signal.SIGTERM, signal.SIGINT):
            try:
                loop.add_signal_handler(sig, lambda: asyncio.create_task(self.stop()))
            except NotImplementedError:
                pass
        
        if hasattr(signal, "SIGHUP"):
            try:
                loop.add_signal_handler(
                    signal.SIGHUP,
                    lambda: (self.load_configurations(), self._wake_event.set()),
                )
            except NotImplementedError:
                pass

        logger.info("backapp daemon started successfully. Low resource mode active.")

        while self.running:
            now = datetime.datetime.now()
            due_jobs: List[str] = []
            earliest_next: Optional[datetime.datetime] = None

            for name, next_dt in self.next_runs.items():
                if next_dt <= now:
                    due_jobs.append(name)
                else:
                    if earliest_next is None or next_dt < earliest_next:
                        earliest_next = next_dt

            # Launch due jobs
            for name in due_jobs:
                if name not in self._running_jobs and name in self.jobs:
                    asyncio.create_task(self.run_job_async(name, manual=False))

            if earliest_next is not None:
                seconds_to_sleep = max(0.5, (earliest_next - datetime.datetime.now()).total_seconds())
                sleep_time = min(seconds_to_sleep, 60.0)
            else:
                sleep_time = 30.0

            try:
                await asyncio.wait_for(self._wake_event.wait(), timeout=sleep_time)
                self._wake_event.clear()
            except asyncio.TimeoutError:
                pass

    async def stop(self) -> None:
        """Gracefully shutdown daemon and IPC server."""
        logger.info("Stopping backapp daemon...")
        self.running = False
        self._wake_event.set()

        if self.ipc_server:
            await self.ipc_server.stop()

        if self._running_jobs:
            logger.info("Waiting for running jobs to complete: %s", list(self._running_jobs))
            for _ in range(30):
                if not self._running_jobs:
                    break
                await asyncio.sleep(0.5)

        logger.info("backapp daemon stopped cleanly.")
