"""Job state tracking, execution history, and in-memory log ring buffer."""

from __future__ import annotations

import collections
import datetime
import logging
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Deque, Dict, List, Optional


@dataclass
class JobRuntimeState:
    name: str
    job_type: str
    schedule: Optional[str]
    status: str = "IDLE"  # IDLE, RUNNING, SUCCESS, FAILED
    last_run_time: Optional[str] = None
    last_run_duration: Optional[float] = None
    last_run_success: Optional[bool] = None
    last_backup_file: Optional[str] = None
    last_backup_size: Optional[int] = None
    last_error: Optional[str] = None
    next_run_time: Optional[str] = None
    keep_last: int = 10
    total_runs: int = 0
    successful_runs: int = 0
    failed_runs: int = 0

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


class RingBufferLogHandler(logging.Handler):
    """Logging handler storing the last N log records in memory for IPC and TUI retrieval."""

    def __init__(self, capacity: int = 250):
        super().__init__()
        self.buffer: Deque[Dict[str, Any]] = collections.deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            msg = self.format(record)
            self.buffer.append({
                "timestamp": datetime.datetime.fromtimestamp(record.created).strftime("%H:%M:%S"),
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            })
        except Exception:
            self.handleError(record)

    def get_logs(self, limit: int = 50) -> List[Dict[str, Any]]:
        items = list(self.buffer)
        return items[-limit:] if limit > 0 else items


class DaemonState:
    """Thread-safe state manager for the backapp daemon."""

    def __init__(self):
        self.start_time: float = time.time()
        self.jobs: Dict[str, JobRuntimeState] = {}
        self.log_handler = RingBufferLogHandler(capacity=300)
        self.log_handler.setFormatter(
            logging.Formatter("%(asctime)s [%(levelname)s] %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
        )

    def register_job(self, name: str, job_type: str, schedule: Optional[str], keep_last: int) -> JobRuntimeState:
        if name not in self.jobs:
            self.jobs[name] = JobRuntimeState(
                name=name,
                job_type=job_type,
                schedule=schedule,
                keep_last=keep_last,
            )
        else:
            self.jobs[name].job_type = job_type
            self.jobs[name].schedule = schedule
            self.jobs[name].keep_last = keep_last
        return self.jobs[name]

    def set_job_running(self, name: str) -> None:
        if name in self.jobs:
            self.jobs[name].status = "RUNNING"
            self.jobs[name].total_runs += 1

    def set_job_completed(
        self,
        name: str,
        success: bool,
        duration: float,
        timestamp: datetime.datetime,
        backup_file: Optional[Path] = None,
        backup_size: Optional[int] = None,
        error_msg: Optional[str] = None,
    ) -> None:
        if name not in self.jobs:
            return

        state = self.jobs[name]
        state.status = "SUCCESS" if success else "FAILED"
        state.last_run_time = timestamp.strftime("%Y-%m-%d %H:%M:%S")
        state.last_run_duration = round(duration, 2)
        state.last_run_success = success
        state.last_backup_file = str(backup_file.name) if backup_file else None
        state.last_backup_size = backup_size
        state.last_error = error_msg

        if success:
            state.successful_runs += 1
        else:
            state.failed_runs += 1

    def set_next_run(self, name: str, next_dt: Optional[datetime.datetime]) -> None:
        if name in self.jobs:
            self.jobs[name].next_run_time = (
                next_dt.strftime("%Y-%m-%d %H:%M:%S") if next_dt else "Manual"
            )

    def get_summary(self) -> Dict[str, Any]:
        uptime_seconds = time.time() - self.start_time
        return {
            "uptime_seconds": round(uptime_seconds, 1),
            "jobs_count": len(self.jobs),
            "jobs": {k: v.to_dict() for k, v in self.jobs.items()},
        }
