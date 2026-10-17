"""Tests for backapp configuration loading and schema validation."""

import pytest
from pathlib import Path
from backapp.config import (
    ConfigError,
    JobConfig,
    RetentionConfig,
    GFSConfig,
    HooksConfig,
    load_jobs_from_directory,
    parse_job_dict,
)


def test_valid_minecraft_job():
    data = {
        "name": "mc_test",
        "schedule": "0 */4 * * *",
        "format": "tar.zst",
        "source_dir": "/data/mc",
        "output_dir": "/backups/mc",
        "retention": {"keep_last": 5},
        "hooks": {
            "pre": ["echo save-off"],
            "post": ["echo save-on"],
        },
    }
    job = parse_job_dict(data)
    assert job.name == "mc_test"
    assert job.schedule == "0 */4 * * *"
    assert job.format == "tar.zst"
    assert job.retention.keep_last == 5
    assert job.hooks.pre == ["echo save-off"]
    assert job.hooks.post == ["echo save-on"]
    assert job.type in ("minecraft", "filesystem")


def test_schedule_cron_aliases():
    assert parse_job_dict({"name": "j1", "schedule": "@daily", "source_dir": "/tmp"}).schedule == "0 0 * * *"
    assert parse_job_dict({"name": "j2", "schedule": "@hourly", "source_dir": "/tmp"}).schedule == "0 * * * *"
    assert parse_job_dict({"name": "j3", "schedule": "every 4 hours", "source_dir": "/tmp"}).schedule == "0 */4 * * *"
    assert parse_job_dict({"name": "j4", "schedule": "every 30 mins", "source_dir": "/tmp"}).schedule == "*/30 * * * *"


def test_gfs_retention_config():
    data = {
        "name": "gfs_job",
        "source_dir": "/tmp",
        "retention": {
            "keep_last": 3,
            "gfs": {
                "keep_daily": 7,
                "keep_weekly": 4,
                "keep_monthly": 12,
                "keep_yearly": 1,
            },
        },
    }
    job = parse_job_dict(data)
    assert job.retention.keep_last == 3
    assert job.retention.gfs is not None
    assert job.retention.gfs.is_enabled is True
    assert job.retention.gfs.keep_daily == 7
    assert job.retention.gfs.keep_weekly == 4
    assert job.retention.gfs.keep_monthly == 12


def test_notifications_config():
    data = {
        "name": "notif_job",
        "source_dir": "/tmp",
        "notifications": {
            "discord_webhook": "https://discord.com/api/webhooks/123/abc",
            "on_start": True,
            "on_success": True,
            "on_failure": True,
        },
    }
    job = parse_job_dict(data)
    assert job.notifications.url == "https://discord.com/api/webhooks/123/abc"
    assert job.notifications.on_start is True


def test_valid_postgres_job():
    data = {
        "name": "pg_test",
        "schedule": "0 2 * * *",
        "format": "dump.zst",
        "output_dir": "/backups/pg",
        "retention": {"keep_last": 7},
        "command": "pg_dump -h localhost -U postgres -d mydb -Fc",
        "output_filename": "mydb_{timestamp}.dump.zst",
    }
    job = parse_job_dict(data)
    assert job.name == "pg_test"
    assert job.format == "dump.zst"
    assert job.type == "postgres"
    assert job.resolve_filename("2026-10-17_15-00-00") == "mydb_2026-10-17_15-00-00.dump.zst"


def test_invalid_cron_schedule():
    data = {
        "name": "invalid_cron",
        "schedule": "not a cron",
        "source_dir": "/data",
    }
    with pytest.raises(ConfigError, match="invalid cron schedule"):
        parse_job_dict(data)


def test_invalid_format():
    data = {
        "name": "invalid_fmt",
        "format": "rar",
        "source_dir": "/data",
    }
    with pytest.raises(ConfigError, match="unsupported format"):
        parse_job_dict(data)


def test_missing_source_dir_for_filesystem():
    data = {
        "name": "no_src",
        "type": "filesystem",
    }
    with pytest.raises(ConfigError, match="must specify 'source_dir'"):
        parse_job_dict(data)


def test_invalid_retention():
    data = {
        "name": "bad_ret",
        "source_dir": "/data",
        "retention": {"keep_last": 0},
    }
    with pytest.raises(ConfigError, match="retention.keep_last must be an integer >= 1"):
        parse_job_dict(data)


def test_load_jobs_from_directory(tmp_path: Path):
    job1_file = tmp_path / "job1.yaml"
    job1_file.write_text("""
name: "job1"
schedule: "0 1 * * *"
format: "tar.gz"
source_dir: "/tmp"
""")

    job2_file = tmp_path / "job2.yml"
    job2_file.write_text("""
name: "job2"
format: "zip"
source_dir: "/tmp"
retention:
  keep_last: 3
""")

    jobs = load_jobs_from_directory(tmp_path)
    assert len(jobs) == 2
    assert "job1" in jobs
    assert "job2" in jobs
    assert jobs["job2"].retention.keep_last == 3


def test_rcon_hooks_config():
    data = {
        "name": "mc_rcon_test",
        "source_dir": "/data/mc",
        "hooks": {
            "rcon": {
                "host": "10.0.0.50",
                "port": 25575,
                "password": "mypassword",
            },
            "pre": ["save-off", "save-all flush"],
            "post": ["save-on"],
        },
    }
    job = parse_job_dict(data)
    assert job.hooks.rcon is not None
    assert job.hooks.rcon.host == "10.0.0.50"
    assert job.hooks.rcon.port == 25575
    assert job.hooks.rcon.password == "mypassword"
    assert job.hooks.pre == ["save-off", "save-all flush"]
    assert job.hooks.post == ["save-on"]


def test_load_mc_survival_example():
    from backapp.config import load_job_file
    example_path = Path(__file__).parent.parent / "examples" / "mc_survival.yaml"
    job = load_job_file(example_path)
    assert job.name == "mc_survival"
    assert job.hooks.rcon is not None
    assert job.hooks.rcon.host == "192.168.1.100"
    assert job.hooks.rcon.port == 25575
    assert job.hooks.pre == ["save-off", "save-all flush"]
    assert job.hooks.post == ["save-on"]
