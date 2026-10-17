"""Retention management for backup archives."""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import List

from backmc.config import JobConfig

logger = logging.getLogger("backmc.retention")


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

        # Match job prefix or if custom output_filename was specified
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


def apply_retention(job: JobConfig) -> List[PrunedFile]:
    """Prune older backups to enforce retention.keep_last."""
    keep_last = job.retention.keep_last
    if keep_last <= 0:
        return []

    # Clean stale temp files in the output directory
    cleanup_stale_tmp_files(job.output_dir)

    all_backups = list_job_backups(job)
    if len(all_backups) <= keep_last:
        return []

    # Files to delete: all items after the first `keep_last`
    to_delete = all_backups[keep_last:]
    pruned: List[PrunedFile] = []

    logger.info(
        "Applying retention for '%s': keeping %d newest files, pruning %d older files.",
        job.name,
        keep_last,
        len(to_delete),
    )

    for file_path in to_delete:
        try:
            size = file_path.stat().st_size
            file_path.unlink()
            pruned.append(PrunedFile(path=file_path, file_size_bytes=size))
            logger.info("Pruned old backup: %s (%d bytes)", file_path.name, size)
        except Exception as e:
            logger.error("Failed to prune backup file %s: %s", file_path, e)

    return pruned
