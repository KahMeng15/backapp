"""Zero-impact backup execution engine with low CPU/IO priority and zero-buffer streaming."""

from __future__ import annotations

import datetime
import logging
import os
import shlex
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from backapp.config import JobConfig
from backapp.hooks import run_post_hooks, run_pre_hooks
from backapp.notifications import notify_backup_completed, notify_backup_started

logger = logging.getLogger("backapp.engine")


class BackupExecutionError(Exception):
    """Raised when backup execution fails."""


@dataclass
class BackupResult:
    job_name: str
    output_file: Path
    file_size_bytes: int
    duration_seconds: float
    timestamp: datetime.datetime
    success: bool
    error_message: Optional[str] = None
    post_hook_errors: Optional[List[str]] = None


# Cache priority wrapper capabilities
_CHECKED_IONICE: bool = False
_CAN_USE_IONICE: bool = False


def check_ionice_support() -> bool:
    """Check if ionice -c 3 is supported in the current container/kernel."""
    global _CHECKED_IONICE, _CAN_USE_IONICE
    if _CHECKED_IONICE:
        return _CAN_USE_IONICE

    _CHECKED_IONICE = True
    if not shutil.which("ionice"):
        _CAN_USE_IONICE = False
        return False

    try:
        res = subprocess.run(
            ["ionice", "-c", "3", "true"],
            capture_output=True,
            timeout=2,
        )
        _CAN_USE_IONICE = (res.returncode == 0)
    except Exception:
        _CAN_USE_IONICE = False

    if not _CAN_USE_IONICE:
        logger.warning(
            "ionice is either not available or permitted (CAP_SYS_ADMIN missing). "
            "Falling back to 'nice -n 19' for CPU priority only."
        )
    return _CAN_USE_IONICE


def get_priority_wrapper() -> List[str]:
    """Return command prefix wrapping process with nice -n 19 and ionice -c 3."""
    wrapper: List[str] = []
    if shutil.which("nice"):
        wrapper.extend(["nice", "-n", "19"])
    if check_ionice_support():
        wrapper.extend(["ionice", "-c", "3"])
    return wrapper


def format_bytes(size: float) -> str:
    """Format bytes into human-readable string (KB, MB, GB)."""
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if size < 1024.0:
            return f"{size:.1f} {unit}"
        size /= 1024.0
    return f"{size:.1f} PB"


def get_restore_command(file_path: Path, target_dir: str = "/restore/target") -> str:
    """Generate standard one-liner shell command to restore the backup file."""
    name = file_path.name.lower()
    if name.endswith(".tar.zst"):
        return f"zstd -d -c '{file_path}' | tar -xf - -C '{target_dir}'"
    elif name.endswith(".tar.gz"):
        return f"tar -xzf '{file_path}' -C '{target_dir}'"
    elif name.endswith(".zip"):
        return f"unzip '{file_path}' -d '{target_dir}'"
    elif name.endswith(".dump.zst"):
        return f"zstd -d -c '{file_path}' | pg_restore -d <database_name>"
    elif name.endswith(".dump.gz"):
        return f"gzip -d -c '{file_path}' | pg_restore -d <database_name>"
    elif name.endswith(".sql.zst"):
        return f"zstd -d -c '{file_path}' | mysql -u root -p <database_name>"
    elif name.endswith(".sql.gz"):
        return f"gzip -d -c '{file_path}' | mysql -u root -p <database_name>"
    else:
        return f"# Restore using appropriate tool for '{file_path.name}'"


class BackupEngine:
    """Handles execution of backup jobs with subprocess streaming and zero RAM buffering."""

    def __init__(self, dry_run: bool = False):
        self.dry_run = dry_run

    def execute_job(self, job: JobConfig) -> BackupResult:
        """Run a backup job following its archetype, hooks, streaming, and retention."""
        start_time = time.monotonic()
        now = datetime.datetime.now()
        timestamp_str = now.strftime("%Y-%m-%d_%H-%M-%S")
        
        job.output_dir.mkdir(parents=True, exist_ok=True)
        dest_filename = job.resolve_filename(timestamp_str)
        dest_path = job.output_dir / dest_filename
        tmp_path = dest_path.with_name(f"{dest_filename}.tmp")

        logger.info("Starting backup job '%s' -> %s", job.name, dest_path)

        # Notify webhook that backup has started
        notify_backup_started(job.name, job.type, dest_path, job.notifications)
        
        post_hook_errors: List[str] = []
        error_msg: Optional[str] = None
        success = False

        try:
            # Step 1: Pre-hooks (e.g. Minecraft save-off, save-all flush)
            if job.hooks.pre:
                logger.info("Executing pre-hooks for '%s'...", job.name)
                run_pre_hooks(job.hooks.pre)

            # Step 2: Stream backup data directly to tmp_path
            if self.dry_run:
                logger.info("[DRY RUN] Would create backup at %s", tmp_path)
                tmp_path.write_text("dry run mock content")
            else:
                self._stream_backup(job, tmp_path)

            # Step 3: Atomic swap
            if tmp_path.exists():
                tmp_path.replace(dest_path)
                success = True
                logger.info(
                    "Backup job '%s' successfully created: %s (%s)",
                    job.name,
                    dest_path.name,
                    format_bytes(dest_path.stat().st_size),
                )
            else:
                raise BackupExecutionError(f"Backup output file {tmp_path} was not created.")

        except Exception as e:
            error_msg = str(e)
            logger.error("Backup job '%s' failed: %s", job.name, error_msg, exc_info=True)
            if tmp_path.exists():
                try:
                    tmp_path.unlink()
                except Exception as unlink_err:
                    logger.warning("Failed to clean up temp file %s: %s", tmp_path, unlink_err)
        finally:
            # Step 4: Post-hooks inside GUARANTEED finally block (e.g. save-on)
            if job.hooks.post:
                logger.info("Executing post-hooks for '%s' (guaranteed finally)...", job.name)
                errs = run_post_hooks(job.hooks.post)
                if errs:
                    post_hook_errors = [str(err) for err in errs]

        duration = time.monotonic() - start_time
        file_size = dest_path.stat().st_size if dest_path.exists() and success else 0

        # Note: final webhook notification is dispatched either here or in daemon after retention
        notify_backup_completed(
            job_name=job.name,
            job_type=job.type,
            output_file=dest_path,
            success=success,
            duration_seconds=duration,
            file_size_formatted=format_bytes(file_size),
            webhook_cfg=job.notifications,
            error_message=error_msg,
        )

        return BackupResult(
            job_name=job.name,
            output_file=dest_path,
            file_size_bytes=file_size,
            duration_seconds=duration,
            timestamp=now,
            success=success,
            error_message=error_msg,
            post_hook_errors=post_hook_errors if post_hook_errors else None,
        )

    def _stream_backup(self, job: JobConfig, tmp_path: Path) -> None:
        """Stream backup data directly between subprocess pipes with zero Python memory buffering."""
        fmt = job.format.lower().lstrip(".")
        priority = get_priority_wrapper()

        # Archetype: Filesystem / Minecraft
        if job.type in ("filesystem", "minecraft") or (job.source_dir and not job.command):
            if not job.source_dir or not job.source_dir.exists():
                raise BackupExecutionError(f"Source directory '{job.source_dir}' does not exist.")

            if fmt == "zip":
                self._stream_zip(job, tmp_path, priority)
            elif fmt in ("tar.zst", "tar.gz"):
                self._stream_tar_compressed(job, tmp_path, fmt, priority)
            else:
                raise BackupExecutionError(f"Unsupported filesystem archive format: {fmt}")

        # Archetype: Database or Custom Command
        elif job.command or job.type in ("postgres", "postgresql", "mysql", "mariadb", "command"):
            self._stream_database_or_command(job, tmp_path, fmt, priority)

        else:
            raise BackupExecutionError(f"Unable to execute job '{job.name}': unknown archetype/type '{job.type}'")

    def _stream_tar_compressed(
        self, job: JobConfig, tmp_path: Path, fmt: str, priority: List[str]
    ) -> None:
        """Execute: tar -C source_dir ... | [zstd|gzip] > tmp_path with zero RAM buffers."""
        tar_cmd = list(priority) + ["tar"]
        for exc in job.exclude:
            tar_cmd.extend(["--exclude", exc])
        tar_cmd.extend(["-C", str(job.source_dir), "-cf", "-", "."])

        if fmt == "tar.zst":
            comp_bin = "zstd"
            if not shutil.which(comp_bin):
                raise BackupExecutionError("zstd utility not found in PATH.")
            comp_cmd = list(priority) + [comp_bin, "-T0", "-3", "-c"]
        elif fmt == "tar.gz":
            comp_bin = "pigz" if shutil.which("pigz") else "gzip"
            comp_cmd = list(priority) + [comp_bin, "-c"]
        else:
            raise BackupExecutionError(f"Unsupported tar compression format: {fmt}")

        logger.debug("Piping: %s | %s > %s", " ".join(tar_cmd), " ".join(comp_cmd), tmp_path)

        with open(tmp_path, "wb") as f_out:
            p_tar = subprocess.Popen(
                tar_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            p_comp = subprocess.Popen(
                comp_cmd,
                stdin=p_tar.stdout,
                stdout=f_out,
                stderr=subprocess.PIPE,
            )
            if p_tar.stdout:
                p_tar.stdout.close()

            _, comp_stderr = p_comp.communicate()
            _, tar_stderr = p_tar.communicate()

        if p_tar.returncode != 0 and p_tar.returncode != 1:
            err = tar_stderr.decode("utf-8", errors="replace").strip()
            if p_tar.returncode > 1 or "error" in err.lower():
                raise BackupExecutionError(f"tar command failed with code {p_tar.returncode}: {err}")

        if p_comp.returncode != 0:
            err = comp_stderr.decode("utf-8", errors="replace").strip()
            raise BackupExecutionError(f"{comp_bin} compressor failed with code {p_comp.returncode}: {err}")

    def _stream_zip(self, job: JobConfig, tmp_path: Path, priority: List[str]) -> None:
        """Execute zip command under priority wrapper."""
        if not shutil.which("zip"):
            raise BackupExecutionError("zip utility not found in PATH.")

        zip_cmd = list(priority) + ["zip", "-q", "-r", str(tmp_path.resolve()), "."]
        for exc in job.exclude:
            zip_cmd.extend(["-x", exc])

        logger.debug("Executing zip: %s", " ".join(zip_cmd))
        proc = subprocess.run(
            zip_cmd,
            cwd=str(job.source_dir),
            capture_output=True,
            text=True,
        )
        if proc.returncode != 0:
            raise BackupExecutionError(f"zip command failed with code {proc.returncode}: {proc.stderr.strip()}")

    def _stream_database_or_command(
        self, job: JobConfig, tmp_path: Path, fmt: str, priority: List[str]
    ) -> None:
        """Execute database dump or custom command and stream stdout directly to compressor/disk."""
        cmd_env = os.environ.copy()

        if job.command:
            dump_cmd_str = os.path.expandvars(job.command)
            dump_args = shlex.split(dump_cmd_str)
        elif job.type in ("postgres", "postgresql"):
            dump_bin = "pg_dump"
            if not shutil.which(dump_bin):
                raise BackupExecutionError(f"{dump_bin} not found in PATH. Install postgresql-client.")
            dump_args = [dump_bin]
            if job.host:
                dump_args.extend(["-h", job.host])
            if job.port:
                dump_args.extend(["-p", str(job.port)])
            if job.user:
                dump_args.extend(["-U", job.user])
            
            if "dump" in fmt:
                dump_args.append("-Fc")

            if job.extra_args:
                dump_args.extend(job.extra_args)

            if job.database:
                dump_args.extend(["-d", job.database])

            pw = job.password
            if not pw and job.password_env:
                pw = os.environ.get(job.password_env, "")
            if pw:
                cmd_env["PGPASSWORD"] = pw

        elif job.type in ("mysql", "mariadb"):
            dump_bin = "mysqldump"
            if not shutil.which(dump_bin):
                raise BackupExecutionError(f"{dump_bin} not found in PATH. Install default-mysql-client.")
            dump_args = [dump_bin]
            dump_args.extend(["--single-transaction", "--quick"])
            
            if job.host:
                dump_args.extend(["-h", job.host])
            if job.port:
                dump_args.extend(["-P", str(job.port)])
            if job.user:
                dump_args.extend(["-u", job.user])

            if job.extra_args:
                dump_args.extend([arg for arg in job.extra_args if arg not in dump_args])

            pw = job.password
            if not pw and job.password_env:
                pw = os.environ.get(job.password_env, "")
            if pw:
                cmd_env["MYSQL_PWD"] = pw

            if job.database:
                dump_args.append(job.database)
        else:
            raise BackupExecutionError(f"Unsupported job type: {job.type}")

        comp_cmd: Optional[List[str]] = None
        if fmt.endswith(".zst") or fmt == "zst":
            if not shutil.which("zstd"):
                raise BackupExecutionError("zstd utility not found in PATH.")
            comp_cmd = list(priority) + ["zstd", "-T0", "-3", "-c"]
        elif fmt.endswith(".gz") or fmt == "gz":
            comp_bin = "pigz" if shutil.which("pigz") else "gzip"
            comp_cmd = list(priority) + [comp_bin, "-c"]

        full_dump_cmd = list(priority) + dump_args

        with open(tmp_path, "wb") as f_out:
            if comp_cmd:
                logger.debug("Piping: %s | %s > %s", " ".join(full_dump_cmd), " ".join(comp_cmd), tmp_path)
                p_dump = subprocess.Popen(
                    full_dump_cmd,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    env=cmd_env,
                )
                p_comp = subprocess.Popen(
                    comp_cmd,
                    stdin=p_dump.stdout,
                    stdout=f_out,
                    stderr=subprocess.PIPE,
                )
                if p_dump.stdout:
                    p_dump.stdout.close()

                _, comp_stderr = p_comp.communicate()
                _, dump_stderr = p_dump.communicate()

                if p_dump.returncode != 0:
                    err = dump_stderr.decode("utf-8", errors="replace").strip()
                    raise BackupExecutionError(f"Dump command failed with code {p_dump.returncode}: {err}")
                if p_comp.returncode != 0:
                    err = comp_stderr.decode("utf-8", errors="replace").strip()
                    raise BackupExecutionError(f"Compression failed with code {p_comp.returncode}: {err}")
            else:
                logger.debug("Streaming: %s > %s", " ".join(full_dump_cmd), tmp_path)
                p_dump = subprocess.Popen(
                    full_dump_cmd,
                    stdout=f_out,
                    stderr=subprocess.PIPE,
                    env=cmd_env,
                )
                _, dump_stderr = p_dump.communicate()
                if p_dump.returncode != 0:
                    err = dump_stderr.decode("utf-8", errors="replace").strip()
                    raise BackupExecutionError(f"Dump command failed with code {p_dump.returncode}: {err}")
