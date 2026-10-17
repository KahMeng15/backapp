"""Tests for backup engine execution, streaming, and restore command generation."""

import shutil
import subprocess
from pathlib import Path
from backapp.config import JobConfig
from backapp.engine import (
    BackupEngine,
    check_ionice_support,
    get_priority_wrapper,
    get_restore_command,
)


def test_priority_wrapper():
    wrapper = get_priority_wrapper()
    if shutil.which("nice"):
        assert "nice" in wrapper
        assert "-n" in wrapper
        assert "19" in wrapper


def test_restore_commands():
    p_zst = Path("/backups/mc_survival_2026-10-17.tar.zst")
    cmd_zst = get_restore_command(p_zst, "/restore/target")
    assert "zstd -d -c" in cmd_zst
    assert "tar -xf -" in cmd_zst

    p_gz = Path("/backups/mc_survival_2026-10-17.tar.gz")
    cmd_gz = get_restore_command(p_gz, "/restore/target")
    assert "tar -xzf" in cmd_gz

    p_zip = Path("/backups/mc_survival_2026-10-17.zip")
    cmd_zip = get_restore_command(p_zip, "/restore/target")
    assert "unzip" in cmd_zip

    p_pg = Path("/backups/prod_db_2026-10-17.dump.zst")
    cmd_pg = get_restore_command(p_pg)
    assert "pg_restore" in cmd_pg


def test_filesystem_tar_gz_backup(tmp_path: Path):
    # Setup mock source data
    src_dir = tmp_path / "source"
    src_dir.mkdir()
    (src_dir / "world").mkdir()
    (src_dir / "world" / "level.dat").write_text("dummy world level data")
    (src_dir / "server.properties").write_text("motd=Test Server\nport=25565")

    out_dir = tmp_path / "backups"

    job = JobConfig(
        name="test_mc",
        format="tar.gz",
        source_dir=src_dir,
        output_dir=out_dir,
    )

    engine = BackupEngine()
    result = engine.execute_job(job)

    assert result.success is True
    assert result.output_file.exists()
    assert result.file_size_bytes > 0
    assert result.output_file.name.endswith(".tar.gz")

    # Verify that the archive can be restored directly with standard tar!
    restore_dir = tmp_path / "restore"
    restore_dir.mkdir()
    tar_res = subprocess.run(
        ["tar", "-xzf", str(result.output_file), "-C", str(restore_dir)],
        capture_output=True,
    )
    assert tar_res.returncode == 0
    assert (restore_dir / "world" / "level.dat").read_text() == "dummy world level data"
    assert "Test Server" in (restore_dir / "server.properties").read_text()


def test_filesystem_tar_zst_backup(tmp_path: Path):
    if not shutil.which("zstd"):
        return

    src_dir = tmp_path / "source_zst"
    src_dir.mkdir()
    (src_dir / "minecraft_world").mkdir()
    (src_dir / "minecraft_world" / "session.lock").write_text("lock_content")
    (src_dir / "minecraft_world" / "world_data.bin").write_text("zstd test world data")

    out_dir = tmp_path / "backups_zst"

    job = JobConfig(
        name="mc_survival",
        format="tar.zst",
        source_dir=src_dir,
        output_dir=out_dir,
    )

    engine = BackupEngine()
    result = engine.execute_job(job)

    assert result.success is True
    assert result.output_file.exists()
    assert result.output_file.name.endswith(".tar.zst")
    assert result.file_size_bytes > 0

    # Test portable restore using standard zstd | tar pipe
    restore_dir = tmp_path / "restore_zst"
    restore_dir.mkdir()
    restore_proc = subprocess.run(
        f"zstd -d -c '{result.output_file}' | tar -xf - -C '{restore_dir}'",
        shell=True,
        capture_output=True,
    )
    assert restore_proc.returncode == 0
    restored_file = restore_dir / "minecraft_world" / "world_data.bin"
    assert restored_file.exists()
    assert restored_file.read_text() == "zstd test world data"


def test_filesystem_zip_backup(tmp_path: Path):
    if not shutil.which("zip"):
        return

    src_dir = tmp_path / "source_zip"
    src_dir.mkdir()
    (src_dir / "file1.txt").write_text("Hello Zip")

    out_dir = tmp_path / "backups_zip"

    job = JobConfig(
        name="test_zip",
        format="zip",
        source_dir=src_dir,
        output_dir=out_dir,
    )

    engine = BackupEngine()
    result = engine.execute_job(job)

    assert result.success is True
    assert result.output_file.exists()
    assert result.output_file.name.endswith(".zip")


def test_command_streaming_backup(tmp_path: Path):
    out_dir = tmp_path / "backups_cmd"

    # Simulate database dump command outputting text
    job = JobConfig(
        name="test_cmd",
        format="sql.gz",
        command="echo 'CREATE TABLE test (id INT); INSERT INTO test VALUES (1);'",
        output_dir=out_dir,
        type="command",
    )

    engine = BackupEngine()
    result = engine.execute_job(job)

    assert result.success is True
    assert result.output_file.exists()
    assert result.output_file.name.endswith(".sql.gz")

    # Verify standard gzip decompression
    gunzip_res = subprocess.run(
        f"gzip -d -c '{result.output_file}'",
        shell=True,
        capture_output=True,
        text=True,
    )
    assert gunzip_res.returncode == 0
    assert "CREATE TABLE test" in gunzip_res.stdout
