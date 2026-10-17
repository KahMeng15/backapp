"""Retention management for backup archives including Grandfather-Father-Son (GFS) policies."""

from __future__ import annotations

import collections
import datetime
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Set

from backapp.config import JobConfig

logger = logging.getLogger("backapp.retention")


@dataclass
class PrunedFile:
    path: Path
    file_size_bytes: int


def list_job_backups(job: JobConfig) -> List[Path]:
    """Find all valid backup archive files for the given job in its output directory."""
    if not job.output_dir.exists() or not job.output_dir.is_dir():
        return []

    backups: List[Path] = []
    prefix = f"{job.name}_"

    for file_path in job.output_dir.iterdir():
        if not file_path.is_file():
            continue
        # Skip temporary files
        if file_path.name.endswith(".tmp"):
            continue

        # Match job prefix or custom output_filename
        if file_path.name.startswith(prefix) or (job.output_filename and job.name in file_path.name):
            backups.append(file_path)

    # Sort descending by mtime (newest first)
    backups.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return backups


def cleanup_stale_tmp_files(output_dir: Path, max_age_seconds: int = 3600) -> List[Path]:
    """Clean up orphan .tmp files left over from interrupted runs older than max_age_seconds."""
    if not output_dir.exists() or not output_dir.is_dir():
        return []

    now = time.time()
    cleaned: List[Path] = []

    for file_path in output_dir.iterdir():
        if file_path.is_file() and file_path.name.endswith(".tmp"):
            try:
                age = now - file_path.stat().st_mtime
                if age > max_age_seconds:
                    logger.warning("Cleaning up stale temporary file: %s (age %.1f hours)", file_path.name, age / 3600)
                    file_path.unlink()
                    cleaned.append(file_path)
            except Exception as e:
                logger.warning("Failed to remove stale tmp file %s: %s", file_path, e)

    return cleaned


def _apply_gfs_retention(job: JobConfig, all_backups: List[Path]) -> List[PrunedFile]:
    """Apply Grandfather-Father-Son (GFS) retention rotation policy.
    
    Categories:
    - Son: Daily snapshots (keep_daily)
    - Father: Weekly snapshots (keep_weekly)
    - Grandfather: Monthly snapshots (keep_monthly)
    - Great-Grandfather: Yearly snapshots (keep_yearly)
    - Plus keep_last most recent backups and optional keep_hourly.
    """
    gfs = job.retention.gfs
    keep_set: Set[Path] = set()

    # 1. Unconditionally keep the most recent N backups
    if job.retention.keep_last > 0:
        for p in all_backups[: job.retention.keep_last]:
            keep_set.add(p)

    if not gfs:
        to_delete = [p for p in all_backups if p not in keep_set]
        return _prune_files(job.name, to_delete)

    # Helper: group backups into periods, selecting the newest snapshot per period
    def group_by_period(files: List[Path], key_fn) -> List[Path]:
        buckets: Dict[Any, Path] = collections.OrderedDict()
        for f in files:
            dt = datetime.datetime.fromtimestamp(f.stat().st_mtime)
            bucket_key = key_fn(dt)
            # Since files is sorted newest first, the first seen in a bucket is the newest
            if bucket_key not in buckets:
                buckets[bucket_key] = f
        return list(buckets.values())

    # Hourly buckets
    if gfs.keep_hourly and gfs.keep_hourly > 0:
        hourly_files = group_by_period(all_backups, lambda dt: (dt.year, dt.month, dt.day, dt.hour))
        for p in hourly_files[: gfs.keep_hourly]:
            keep_set.add(p)

    # Daily buckets (Son)
    if gfs.keep_daily and gfs.keep_daily > 0:
        daily_files = group_by_period(all_backups, lambda dt: (dt.year, dt.month, dt.day))
        for p in daily_files[: gfs.keep_daily]:
            keep_set.add(p)

    # Weekly buckets (Father)
    if gfs.keep_weekly and gfs.keep_weekly > 0:
        weekly_files = group_by_period(all_backups, lambda dt: dt.isocalendar()[:2])
        for p in weekly_files[: gfs.keep_weekly]:
            keep_set.add(p)

    # Monthly buckets (Grandfather)
    if gfs.keep_monthly and gfs.keep_monthly > 0:
        monthly_files = group_by_period(all_backups, lambda dt: (dt.year, dt.month))
        for p in monthly_files[: gfs.keep_monthly]:
            keep_set.add(p)

    # Yearly buckets (Great-Grandfather)
    if gfs.keep_yearly and gfs.keep_yearly > 0:
        yearly_files = group_by_period(all_backups, lambda dt: dt.year)
        for p in yearly_files[: gfs.keep_yearly]:
            keep_set.add(p)

    to_delete = [p for p in all_backups if p not in keep_set]
    return _prune_files(job.name, to_delete)


def _prune_files(job_name: str, to_delete: List[Path]) -> List[PrunedFile]:
    pruned: List[PrunedFile] = []
    if not to_delete:
        return pruned

    logger.info("Pruning %d old backup(s) for job '%s'...", len(to_delete), job_name)
    for file_path in to_delete:
        try:
            size = file_path.stat().st_size
            file_path.unlink()
            pruned.append(PrunedFile(path=file_path, file_size_bytes=size))
            logger.info("Pruned old backup: %s (%d bytes)", file_path.name, size)
        except Exception as e:
            logger.error("Failed to prune backup file %s: %s", file_path, e)
    return pruned


def apply_retention(job: JobConfig) -> List[PrunedFile]:
    """Prune older backups to enforce retention policy (Simple keep_last or GFS)."""
    # Clean stale temp files in the output directory
    cleanup_stale_tmp_files(job.output_dir)

    all_backups = list_job_backups(job)
    if not all_backups:
        return []

    # If GFS is configured, apply GFS retention logic
    if job.retention.gfs and job.retention.gfs.is_enabled:
        return _apply_gfs_retention(job, all_backups)

    # Standard simple keep_last retention
    keep_last = job.retention.keep_last
    if keep_last <= 0 or len(all_backups) <= keep_last:
        return []

    to_delete = all_backups[keep_last:]
    return _prune_files(job.name, to_delete)
