"""Tests for backup retention and GFS policies."""

import os
import time
from pathlib import Path
from backapp.config import GFSConfig, JobConfig, RetentionConfig
from backapp.retention import apply_retention, cleanup_stale_tmp_files, list_job_backups


def test_simple_retention_pruning(tmp_path: Path):
    output_dir = tmp_path / "backups"
    output_dir.mkdir()

    job = JobConfig(
        name="test_job",
        output_dir=output_dir,
        retention=RetentionConfig(keep_last=3),
    )

    now = time.time()
    for i in range(5):
        f = output_dir / f"test_job_2026-10-17_15-0{i}-00.tar.zst"
        f.write_text(f"backup {i}")
        os_time = now - (100 * (4 - i))
        f.touch()
        os.utime(f, (os_time, os_time))

    assert len(list_job_backups(job)) == 5
    pruned = apply_retention(job)
    assert len(pruned) == 2

    remaining = list_job_backups(job)
    assert len(remaining) == 3
    remaining_names = [f.name for f in remaining]
    assert "test_job_2026-10-17_15-04-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-03-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-02-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-00-00.tar.zst" not in remaining_names


def test_gfs_retention_pruning(tmp_path: Path):
    output_dir = tmp_path / "backups_gfs"
    output_dir.mkdir()

    job = JobConfig(
        name="gfs_test",
        output_dir=output_dir,
        retention=RetentionConfig(
            keep_last=2, # Keep last 2 unconditionally
            gfs=GFSConfig(
                keep_daily=3,   # Keep 1 per day for 3 days
                keep_weekly=2,  # Keep 1 per week for 2 weeks
                keep_monthly=2, # Keep 1 per month for 2 months
            ),
        ),
    )

    # Generate archives across different days and months
    now = time.time()
    day_seconds = 86400

    # Day 0: 3 backups today (should keep newest today, plus keep_last=2)
    f0_1 = output_dir / "gfs_test_day0_1.tar.zst"
    f0_1.write_text("today 1")
    os.utime(f0_1, (now - 3600, now - 3600))

    f0_2 = output_dir / "gfs_test_day0_2.tar.zst"
    f0_2.write_text("today 2")
    os.utime(f0_2, (now, now))

    # Day 1 ago
    f1 = output_dir / "gfs_test_day1.tar.zst"
    f1.write_text("day 1")
    os.utime(f1, (now - day_seconds, now - day_seconds))

    # Day 2 ago
    f2 = output_dir / "gfs_test_day2.tar.zst"
    f2.write_text("day 2")
    os.utime(f2, (now - (2 * day_seconds), now - (2 * day_seconds)))

    # Day 5 ago (same week or previous week)
    f5 = output_dir / "gfs_test_day5.tar.zst"
    f5.write_text("day 5")
    os.utime(f5, (now - (5 * day_seconds), now - (5 * day_seconds)))

    # Day 40 ago (previous month)
    f40 = output_dir / "gfs_test_day40.tar.zst"
    f40.write_text("month ago")
    os.utime(f40, (now - (40 * day_seconds), now - (40 * day_seconds)))

    # Day 90 ago (3 months ago - exceeds keep_monthly=2, so should be pruned)
    f90 = output_dir / "gfs_test_day90.tar.zst"
    f90.write_text("old 90")
    os.utime(f90, (now - (90 * day_seconds), now - (90 * day_seconds)))

    # Day 120 ago (4 months ago - should be pruned)
    f120 = output_dir / "gfs_test_day120.tar.zst"
    f120.write_text("old 120")
    os.utime(f120, (now - (120 * day_seconds), now - (120 * day_seconds)))

    pruned = apply_retention(job)
    pruned_names = [p.path.name for p in pruned]

    # f90 and f120 should be pruned
    assert "gfs_test_day90.tar.zst" in pruned_names
    assert "gfs_test_day120.tar.zst" in pruned_names

    # Today's, yesterday's, and month ago should be preserved
    remaining_names = [p.name for p in list_job_backups(job)]
    assert "gfs_test_day0_2.tar.zst" in remaining_names
    assert "gfs_test_day1.tar.zst" in remaining_names
    assert "gfs_test_day40.tar.zst" in remaining_names


def test_cleanup_stale_tmp_files(tmp_path: Path):
    output_dir = tmp_path / "backups"
    output_dir.mkdir()

    fresh_tmp = output_dir / "job_fresh.tar.zst.tmp"
    fresh_tmp.write_text("in progress")

    stale_tmp = output_dir / "job_stale.tar.zst.tmp"
    stale_tmp.write_text("abandoned")
    stale_time = time.time() - 7200
    os.utime(stale_tmp, (stale_time, stale_time))

    cleaned = cleanup_stale_tmp_files(output_dir, max_age_seconds=3600)
    assert len(cleaned) == 1
    assert cleaned[0].name == "job_stale.tar.zst.tmp"
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()
