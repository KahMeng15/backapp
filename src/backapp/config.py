"""Configuration parser, GFS retention specs, and schema validator for backapp."""

from __future__ import annotations

import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, List, Optional
import yaml
from croniter import croniter

from backapp.notifications import WebhookConfig


class ConfigError(Exception):
    """Raised when a job configuration file has errors."""


# Friendly schedule aliases mapping to standard 5-part cron syntax
CRON_ALIASES = {
    "@hourly": "0 * * * *",
    "@daily": "0 0 * * *",
    "@midnight": "0 0 * * *",
    "@weekly": "0 0 * * 0",
    "@monthly": "0 0 1 * *",
    "@yearly": "0 0 1 1 *",
    "@annually": "0 0 1 1 *",
}


def normalize_schedule(schedule_raw: Optional[str]) -> Optional[str]:
    """Convert cron aliases (e.g. @daily, @hourly) or interval phrases to standard cron syntax."""
    if not schedule_raw:
        return None
    s = schedule_raw.strip()
    if s.lower() in CRON_ALIASES:
        return CRON_ALIASES[s.lower()]

    # Match simple patterns like "every X hours" or "every X minutes"
    m_hours = re.match(r"^every\s+(\d+)\s+hours?$", s, re.IGNORECASE)
    if m_hours:
        h = int(m_hours.group(1))
        return f"0 */{h} * * *"

    m_mins = re.match(r"^every\s+(\d+)\s+min(?:ute)?s?$", s, re.IGNORECASE)
    if m_mins:
        m = int(m_mins.group(1))
        return f"*/{m} * * * *"

    return s


@dataclass
class GFSConfig:
    """Grandfather-Father-Son (GFS) retention rotation policy."""
    keep_hourly: Optional[int] = None
    keep_daily: Optional[int] = None    # Son (daily snapshots)
    keep_weekly: Optional[int] = None   # Father (weekly snapshots)
    keep_monthly: Optional[int] = None  # Grandfather (monthly snapshots)
    keep_yearly: Optional[int] = None   # Great-grandfather (yearly snapshots)

    @property
    def is_enabled(self) -> bool:
        return any(
            v is not None and v > 0
            for v in (self.keep_hourly, self.keep_daily, self.keep_weekly, self.keep_monthly, self.keep_yearly)
        )


@dataclass
class RetentionConfig:
    keep_last: int = 10
    gfs: Optional[GFSConfig] = None


@dataclass
class RconConfig:
    """RCON connection parameters for Minecraft and game servers."""
    host: str = "127.0.0.1"
    port: int = 25575
    password: Optional[str] = None


@dataclass
class HooksConfig:
    pre: List[str] = field(default_factory=list)
    post: List[str] = field(default_factory=list)
    rcon: Optional[RconConfig] = None


@dataclass
class JobConfig:
    name: str
    schedule: Optional[str] = None
    format: str = "tar.zst"
    output_dir: Path = Path("/mnt/backups")
    retention: RetentionConfig = field(default_factory=RetentionConfig)
    hooks: HooksConfig = field(default_factory=HooksConfig)
    notifications: WebhookConfig = field(default_factory=WebhookConfig)
    
    # Archetype detection: 'filesystem' | 'minecraft' | 'postgres' | 'mysql' | 'command'
    type: str = "filesystem"
    
    # Filesystem / Minecraft archetype
    source_dir: Optional[Path] = None
    exclude: List[str] = field(default_factory=list)
    
    # Database / generic command parameters
    command: Optional[str] = None
    host: Optional[str] = None
    port: Optional[int] = None
    user: Optional[str] = None
    password: Optional[str] = None
    password_env: Optional[str] = None
    database: Optional[str] = None
    extra_args: List[str] = field(default_factory=list)
    
    # Custom output filename pattern: e.g. "prod_db_{timestamp}.dump.zst"
    output_filename: Optional[str] = None
    
    # Raw source path for display/reference
    config_file: Optional[Path] = None

    def validate(self) -> None:
        """Validate the job configuration."""
        if not self.name or not re.match(r"^[a-zA-Z0-9_\-]+$", self.name):
            raise ConfigError(
                f"Job name '{self.name}' must contain only alphanumeric characters, underscores, and hyphens."
            )

        if self.schedule is not None and self.schedule.strip() != "":
            if not croniter.is_valid(self.schedule):
                raise ConfigError(
                    f"Job '{self.name}' has invalid cron schedule '{self.schedule}'. Expected standard 5-part cron syntax (e.g. '0 */4 * * *')."
                )

        # Normalize format
        fmt = self.format.lower().lstrip(".")
        allowed_formats = {
            "tar.zst", "tar.gz", "zip",
            "dump.zst", "dump.gz",
            "sql.zst", "sql.gz",
        }
        if fmt not in allowed_formats:
            raise ConfigError(
                f"Job '{self.name}' specifies unsupported format '{self.format}'. "
                f"Supported formats: {', '.join(sorted(allowed_formats))}"
            )

        # Archetype validation
        if self.type in ("filesystem", "minecraft"):
            if not self.source_dir and not self.command:
                raise ConfigError(
                    f"Job '{self.name}' (type={self.type}) must specify 'source_dir'."
                )
        elif self.type in ("postgres", "postgresql"):
            if not self.command and not self.database:
                raise ConfigError(
                    f"PostgreSQL job '{self.name}' must specify 'database' (or custom 'command')."
                )
        elif self.type in ("mysql", "mariadb"):
            if not self.command and not self.database:
                raise ConfigError(
                    f"MySQL job '{self.name}' must specify 'database' (or custom 'command')."
                )
        elif self.type == "command":
            if not self.command:
                raise ConfigError(
                    f"Command job '{self.name}' must specify 'command'."
                )

        if self.retention.keep_last <= 0:
            raise ConfigError(
                f"Job '{self.name}' retention.keep_last must be an integer >= 1 (got {self.retention.keep_last})."
            )

    def resolve_filename(self, timestamp_str: str) -> str:
        """Resolve destination filename based on pattern or default naming convention."""
        fmt = self.format.lower().lstrip(".")
        if self.output_filename:
            return self.output_filename.format(
                timestamp=timestamp_str,
                job_name=self.name,
                ext=fmt,
            )
        return f"{self.name}_{timestamp_str}.{fmt}"


def parse_job_dict(data: dict[str, Any], config_file: Optional[Path] = None) -> JobConfig:
    """Parse dictionary loaded from YAML into a JobConfig instance."""
    if not isinstance(data, dict):
        raise ConfigError(f"Job file must contain a YAML dictionary, got {type(data).__name__}")

    name = data.get("name")
    if not name:
        if config_file:
            name = config_file.stem
        else:
            raise ConfigError("Job configuration missing required field 'name'")

    schedule_raw = data.get("schedule")
    schedule = normalize_schedule(schedule_raw)
    fmt = data.get("format", "tar.zst")
    output_dir = Path(os.path.expandvars(str(data.get("output_dir", "/mnt/backups"))))

    # Retention (supporting keep_last and GFS)
    ret_raw = data.get("retention", {})
    gfs_config: Optional[GFSConfig] = None
    if isinstance(ret_raw, int):
        keep_last = ret_raw
    elif isinstance(ret_raw, dict):
        keep_last = ret_raw.get("keep_last", 10)
        gfs_raw = ret_raw.get("gfs")
        if isinstance(gfs_raw, dict):
            gfs_config = GFSConfig(
                keep_hourly=gfs_raw.get("keep_hourly"),
                keep_daily=gfs_raw.get("keep_daily"),
                keep_weekly=gfs_raw.get("keep_weekly"),
                keep_monthly=gfs_raw.get("keep_monthly"),
                keep_yearly=gfs_raw.get("keep_yearly"),
            )
    else:
        keep_last = 10
    retention = RetentionConfig(keep_last=int(keep_last), gfs=gfs_config)

    # Hooks & RCON configuration
    hooks_raw = data.get("hooks", {})
    pre_hooks = hooks_raw.get("pre", []) if isinstance(hooks_raw, dict) else []
    post_hooks = hooks_raw.get("post", []) if isinstance(hooks_raw, dict) else []
    if isinstance(pre_hooks, str):
        pre_hooks = [pre_hooks]
    if isinstance(post_hooks, str):
        post_hooks = [post_hooks]

    rcon_raw = (hooks_raw.get("rcon") if isinstance(hooks_raw, dict) else None) or data.get("rcon")
    rcon_config: Optional[RconConfig] = None
    if isinstance(rcon_raw, dict):
        rcon_config = RconConfig(
            host=str(rcon_raw.get("host", "127.0.0.1")),
            port=int(rcon_raw.get("port", 25575)),
            password=str(rcon_raw.get("password", "")) if rcon_raw.get("password") is not None else None,
        )

    hooks = HooksConfig(pre=list(pre_hooks), post=list(post_hooks), rcon=rcon_config)

    # Notifications / Webhooks
    notif_raw = data.get("notifications") or data.get("webhook") or data.get("webhooks")
    notifications = WebhookConfig.from_dict(notif_raw)

    # Determine archetype type
    job_type = data.get("type")
    source_dir_raw = data.get("source_dir")
    source_dir = Path(os.path.expandvars(str(source_dir_raw))) if source_dir_raw else None
    command = data.get("command")

    if not job_type:
        if command and ("pg_dump" in command or "postgres" in command):
            job_type = "postgres"
        elif command and ("mysqldump" in command or "mariadb" in command or "mysql" in command):
            job_type = "mysql"
        elif command:
            job_type = "command"
        elif source_dir:
            job_type = "minecraft" if "mc" in name.lower() or "minecraft" in name.lower() else "filesystem"
        else:
            job_type = "filesystem"

    exclude = data.get("exclude", [])
    if isinstance(exclude, str):
        exclude = [exclude]

    job = JobConfig(
        name=name,
        schedule=schedule,
        format=fmt,
        output_dir=output_dir,
        retention=retention,
        hooks=hooks,
        notifications=notifications,
        type=job_type,
        source_dir=source_dir,
        exclude=list(exclude),
        command=command,
        host=data.get("host"),
        port=data.get("port"),
        user=data.get("user"),
        password=data.get("password"),
        password_env=data.get("password_env"),
        database=data.get("database"),
        extra_args=list(data.get("extra_args", [])),
        output_filename=data.get("output_filename"),
        config_file=config_file,
    )
    job.validate()
    return job


def load_job_file(file_path: Path) -> JobConfig:
    """Load a single job YAML file."""
    if not file_path.is_file():
        raise ConfigError(f"Job configuration file not found: {file_path}")
    
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            data = yaml.safe_load(f)
        except yaml.YAMLError as e:
            raise ConfigError(f"Failed to parse YAML file {file_path}: {e}") from e

    return parse_job_dict(data or {}, config_file=file_path)


def load_jobs_from_directory(directory: Path) -> dict[str, JobConfig]:
    """Parse all *.yaml and *.yml files from the specified jobs directory."""
    jobs: dict[str, JobConfig] = {}
    if not directory.exists() or not directory.is_dir():
        return jobs

    for file_path in sorted(directory.iterdir()):
        if file_path.suffix.lower() in (".yaml", ".yml") and not file_path.name.startswith("."):
            try:
                job = load_job_file(file_path)
                jobs[job.name] = job
            except Exception as e:
                raise ConfigError(f"Error loading {file_path.name}: {e}") from e

    return jobs


def get_template_files() -> list[Path]:
    """Retrieve bundled template YAML configurations."""
    # 1. In Docker image at /app/examples
    docker_examples = Path("/app/examples")
    if docker_examples.is_dir():
        files = sorted(docker_examples.glob("*.yaml"))
        if files:
            return files

    # 2. In repository root examples/
    repo_examples = Path(__file__).resolve().parent.parent.parent / "examples"
    if repo_examples.is_dir():
        files = sorted(repo_examples.glob("*.yaml"))
        if files:
            return files

    return []


def seed_examples_if_empty(target_dir: Path) -> list[str]:
    """Auto-seed bundled example configs into target_dir if it exists/can be created and is empty."""
    created: list[str] = []
    try:
        target_dir.mkdir(parents=True, exist_ok=True)
        existing = [
            f for f in target_dir.iterdir()
            if f.suffix.lower() in (".yaml", ".yml") and not f.name.startswith(".")
        ]
        if not existing:
            templates = get_template_files()
            for tmpl in templates:
                dest = target_dir / tmpl.name
                shutil.copy2(tmpl, dest)
                created.append(tmpl.name)
    except (OSError, PermissionError):
        # Ignore if target_dir is mounted read-only or uncreatable
        pass
    return created
