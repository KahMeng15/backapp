"""Tests for backup retention and cleanup."""

import time
from pathlib import Path
from backmc.config import JobConfig, RetentionConfig
from backmc.retention import apply_retention, cleanup_stale_tmp_files, list_job_backups


def test_retention_pruning(tmp_path: Path):
    output_dir = tmp_path / "backups"
    output_dir.mkdir()

    job = JobConfig(
        name="test_job",
        output_dir=output_dir,
        retention=RetentionConfig(keep_last=3),
    )

    # Create 5 backup files with staggered timestamps
    now = time.time()
    created_files = []
    for i in range(5):
        f = output_dir / f"test_job_2026-10-17_15-0{i}-00.tar.zst"
        f.write_text(f"backup {i}")
        # Adjust mtime so file 4 is newest, file 0 is oldest
        os_time = now - (100 * (4 - i))
        f.touch()
        import os
        os.utime(f, (os_time, os_time))
        created_files.append(f)

    # Before retention: 5 files
    assert len(list_job_backups(job)) == 5

    # Apply retention (keep_last = 3)
    pruned = apply_retention(job)
    assert len(pruned) == 2

    # After retention: exactly 3 newest files remain
    remaining = list_job_backups(job)
    assert len(remaining) == 3
    remaining_names = [f.name for f in remaining]
    assert "test_job_2026-10-17_15-04-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-03-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-02-00.tar.zst" in remaining_names
    assert "test_job_2026-10-17_15-00-00.tar.zst" not in remaining_names


def test_cleanup_stale_tmp_files(tmp_path: Path):
    output_dir = tmp_path / "backups"
    output_dir.mkdir()

    fresh_tmp = output_dir / "job_fresh.tar.zst.tmp"
    fresh_tmp.write_text("in progress")

    stale_tmp = output_dir / "job_stale.tar.zst.tmp"
    stale_tmp.write_text("abandoned")
    # Make stale_tmp 2 hours old
    stale_time = time.time() - 7200
    import os
    os.utime(stale_tmp, (stale_time, stale_time))

    cleaned = cleanup_stale_tmp_files(output_dir, max_age_seconds=3600)
    assert len(cleaned) == 1
    assert cleaned[0].name == "job_stale.tar.zst.tmp"
    assert not stale_tmp.exists()
    assert fresh_tmp.exists()
