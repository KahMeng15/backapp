"""Tests for backmc configuration loading and schema validation."""

import pytest
from pathlib import Path
from backmc.config import ConfigError, JobConfig, RetentionConfig, HooksConfig, load_jobs_from_directory, parse_job_dict


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
