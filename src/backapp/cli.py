"""CLI interface for backapp daemon, TUI, and command-line operations."""

from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path
from typing import Optional

from rich.console import Console
from rich.table import Table

from backapp import __version__
from backapp.config import ConfigError, get_template_files, load_job_file, load_jobs_from_directory
from backapp.daemon import BackupDaemon
from backapp.engine import BackupEngine, format_bytes, get_restore_command
from backapp.ipc import IPCClient, resolve_socket_path
from backapp.retention import apply_retention, list_job_backups

console = Console()


def setup_logging(level_name: str = "INFO") -> None:
    level = getattr(logging, level_name.upper(), logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )


def cmd_daemon(args: argparse.Namespace) -> int:
    """Run the backapp scheduler daemon."""
    setup_logging(args.log_level)
    jobs_dir = Path(args.jobs_dir)
    socket_path = Path(args.socket_path) if args.socket_path else resolve_socket_path()

    console.print(f"[bold cyan]backapp daemon v{__version__}[/bold cyan]")
    console.print(f"Jobs directory: [green]{jobs_dir}[/green]")
    console.print(f"IPC socket:     [green]{socket_path}[/green]")
    console.print("Priority wrap:  [bold yellow]nice -n 19 ionice -c 3[/bold yellow]")

    daemon = BackupDaemon(
        jobs_dir=jobs_dir,
        socket_path=socket_path,
        dry_run=args.dry_run,
    )
    try:
        asyncio.run(daemon.start())
    except (KeyboardInterrupt, SystemExit):
        pass
    return 0


def cmd_tui(args: argparse.Namespace) -> int:
    """Launch the interactive Textual TUI dashboard."""
    from backapp.tui import BackappApp

    jobs_dir = Path(args.jobs_dir)
    socket_path = Path(args.socket_path) if args.socket_path else resolve_socket_path()

    app = BackappApp(jobs_dir=jobs_dir, socket_path=socket_path)
    app.run()
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Run a specific backup job or all jobs immediately."""
    setup_logging(args.log_level)
    jobs_dir = Path(args.jobs_dir)
    socket_path = Path(args.socket_path) if args.socket_path else resolve_socket_path()
    ipc = IPCClient(socket_path)

    run_all = args.all or (args.job and args.job.lower() in ("all", "*"))
    target_job = None if run_all else args.job

    if not run_all and not target_job:
        console.print("[bold red]Error: Please specify a job name to run, or pass --all to run all jobs.[/bold red]")
        console.print("Usage: backapp run <job_name>  OR  backapp run --all")
        return 1

    # If daemon is running, ask daemon to trigger
    if not args.direct and ipc.is_daemon_running():
        console.print(f"[cyan]Daemon active on socket {socket_path}. Sending trigger request...[/cyan]")
        req_payload = {"action": "trigger_all"} if run_all else {"action": "trigger", "job_name": target_job}
        resp = ipc.send_request(req_payload)
        if resp.get("status") == "ok":
            console.print(f"[bold green]✔ {resp.get('message')}[/bold green]")
            return 0
        else:
            console.print(f"[bold red]✖ Daemon error: {resp.get('message')}[/bold red]")
            return 1

    # Otherwise execute directly in standalone mode
    jobs = load_jobs_from_directory(jobs_dir)
    if not jobs:
        console.print(f"[bold red]No job configurations found in {jobs_dir}[/bold red]")
        return 1

    if run_all:
        jobs_to_run = list(jobs.values())
        console.print(f"[yellow]Running all {len(jobs_to_run)} backup jobs in direct mode...[/yellow]")
    else:
        if target_job not in jobs:
            console.print(f"[bold red]Error: Job '{target_job}' not found in {jobs_dir}[/bold red]")
            return 1
        jobs_to_run = [jobs[target_job]]
        console.print(f"[yellow]Running job '{target_job}' in direct mode...[/yellow]")

    engine = BackupEngine(dry_run=args.dry_run)
    all_success = True

    for job in jobs_to_run:
        console.print(f"\n[cyan]▶ Executing backup for '{job.name}' ({job.type})...[/cyan]")
        result = engine.execute_job(job)

        if result.success:
            console.print(f"[bold green]✔ Backup successfully created:[/bold green] {result.output_file}")
            console.print(f"  Size: {format_bytes(result.file_size_bytes)} | Duration: {result.duration_seconds:.2f}s")
            if not args.dry_run:
                pruned = apply_retention(job)
                if pruned:
                    console.print(f"  [yellow]Retention pruned {len(pruned)} older archive(s)[/yellow]")
        else:
            all_success = False
            console.print(f"[bold red]✖ Backup failed for '{job.name}': {result.error_message}[/bold red]")
            if result.post_hook_errors:
                console.print(f"  [red]Post-hook errors: {result.post_hook_errors}[/red]")

    return 0 if all_success else 1


def cmd_list(args: argparse.Namespace) -> int:
    """List all configured jobs and their status."""
    jobs_dir = Path(args.jobs_dir)
    socket_path = Path(args.socket_path) if args.socket_path else resolve_socket_path()
    ipc = IPCClient(socket_path)

    table = Table(title=f"backapp Configured Jobs ({jobs_dir})", header_style="bold cyan")
    table.add_column("Job Name", style="bold white")
    table.add_column("Type")
    table.add_column("Schedule")
    table.add_column("Format")
    table.add_column("Output Dir")
    table.add_column("Retention Policy")
    table.add_column("Backups Count")

    try:
        jobs = load_jobs_from_directory(jobs_dir)
    except Exception as e:
        console.print(f"[bold red]Failed loading jobs: {e}[/bold red]")
        return 1

    if not jobs:
        console.print(f"[dim]No job configuration files found in {jobs_dir}[/dim]")
        return 0

    for name, job in jobs.items():
        backups = list_job_backups(job)
        if job.retention.gfs and job.retention.gfs.is_enabled:
            g = job.retention.gfs
            parts = []
            if g.keep_daily: parts.append(f"D:{g.keep_daily}")
            if g.keep_weekly: parts.append(f"W:{g.keep_weekly}")
            if g.keep_monthly: parts.append(f"M:{g.keep_monthly}")
            if g.keep_yearly: parts.append(f"Y:{g.keep_yearly}")
            ret_str = f"GFS ({', '.join(parts)})"
        else:
            ret_str = f"Last {job.retention.keep_last}"

        table.add_row(
            job.name,
            job.type,
            job.schedule or "Manual",
            job.format,
            str(job.output_dir),
            ret_str,
            str(len(backups)),
        )

    console.print(table)
    return 0


def cmd_backups(args: argparse.Namespace) -> int:
    """List existing backup archives on disk for one or all jobs."""
    jobs_dir = Path(args.jobs_dir)
    jobs = load_jobs_from_directory(jobs_dir)

    target_jobs = [jobs[args.job]] if args.job and args.job in jobs else list(jobs.values())
    if not target_jobs:
        console.print(f"[yellow]No matching jobs found in {jobs_dir}[/yellow]")
        return 0

    for job in target_jobs:
        files = list_job_backups(job)
        console.print(f"\n[bold cyan]Backups for job '{job.name}' (output_dir: {job.output_dir}):[/bold cyan]")
        if not files:
            console.print("  [dim]No backup archives found.[/dim]")
            continue

        table = Table(header_style="bold magenta")
        table.add_column("Filename", style="bold white")
        table.add_column("Size")
        table.add_column("Restore Command", style="green")

        for f in files:
            stat = f.stat()
            table.add_row(
                f.name,
                format_bytes(stat.st_size),
                get_restore_command(f),
            )
        console.print(table)

    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    """Validate all YAML job configuration files in the jobs directory."""
    jobs_dir = Path(args.jobs_dir)
    console.print(f"[bold]Validating configuration files in {jobs_dir}...[/bold]")
    if not jobs_dir.exists():
        console.print(f"[bold red]Directory {jobs_dir} does not exist.[/bold red]")
        return 1

    yaml_files = list(jobs_dir.glob("*.yaml")) + list(jobs_dir.glob("*.yml"))
    if not yaml_files:
        console.print(f"[yellow]No .yaml or .yml files found in {jobs_dir}[/yellow]")
        return 0

    errors = 0
    for f in sorted(yaml_files):
        try:
            job = load_job_file(f)
            ret_mode = "GFS" if (job.retention.gfs and job.retention.gfs.is_enabled) else f"keep_last={job.retention.keep_last}"
            webhook_status = "webhook enabled" if job.notifications.url else "no webhook"
            console.print(f"  [green]✔ {f.name}[/green] -> valid (job={job.name}, type={job.type}, fmt={job.format}, {ret_mode}, {webhook_status})")
        except ConfigError as e:
            console.print(f"  [red]✖ {f.name}[/red] -> validation error: {e}")
            errors += 1
        except Exception as e:
            console.print(f"  [red]✖ {f.name}[/red] -> syntax error: {e}")
            errors += 1

    if errors == 0:
        console.print(f"[bold green]All {len(yaml_files)} configuration file(s) are valid![/bold green]")
        return 0
    else:
        console.print(f"[bold red]Validation failed with {errors} error(s).[/bold red]")
        return 1


def cmd_init(args: argparse.Namespace) -> int:
    """Initialize template YAML configurations into target directory."""
    import shutil
    dest = Path(args.dest)
    dest.mkdir(parents=True, exist_ok=True)
    templates = get_template_files()
    if not templates:
        console.print("[bold red]No template configurations found packaged with backapp.[/bold red]")
        return 1

    console.print(f"[bold]Initializing templates into [green]{dest.resolve()}[/green]...[/bold]")
    created = 0
    for tmpl in templates:
        target_file = dest / tmpl.name
        if target_file.exists() and not args.force:
            console.print(f"  [yellow]Skipped[/yellow] {tmpl.name} (already exists)")
        else:
            shutil.copy2(tmpl, target_file)
            console.print(f"  [green]✔ Created[/green] {target_file.name}")
            created += 1

    console.print(f"[bold green]Successfully initialized {created} template(s) in {dest}![/bold green]")
    return 0


def main() -> None:
    default_jobs_dir = os.environ.get("BACKUP_JOBS_DIR", "/etc/backup/jobs")

    common_parser = argparse.ArgumentParser(add_help=False)
    common_parser.add_argument(
        "--jobs-dir",
        default=default_jobs_dir,
        help=f"Directory containing *.yaml backup job configs (default: {default_jobs_dir})",
    )
    common_parser.add_argument(
        "--socket-path",
        default=None,
        help="Path to IPC UNIX domain socket (default: /var/run/backapp/backapp.sock or /tmp/backapp.sock)",
    )
    common_parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging verbosity (default: INFO)",
    )

    parser = argparse.ArgumentParser(
        prog="backapp",
        description="Zero-impact production backup daemon and TUI for Minecraft, PostgreSQL, and MySQL.",
        parents=[common_parser],
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")

    subparsers = parser.add_subparsers(dest="subcommand", help="Sub-commands")

    # daemon
    p_daemon = subparsers.add_parser("daemon", parents=[common_parser], help="Run background backup scheduler daemon")
    p_daemon.add_argument("--dry-run", action="store_true", help="Simulate backup executions without writing archives")

    # tui
    subparsers.add_parser("tui", parents=[common_parser], help="Launch interactive Terminal User Interface (TUI)")

    # run
    p_run = subparsers.add_parser("run", parents=[common_parser], help="Trigger a specific backup job or all jobs immediately")
    p_run.add_argument("job", nargs="?", default=None, help="Name of the job to run (or 'all' / omit when using --all)")
    p_run.add_argument("--all", action="store_true", help="Trigger all configured backup jobs immediately")
    p_run.add_argument("--direct", action="store_true", help="Bypass daemon and execute directly in current process")
    p_run.add_argument("--dry-run", action="store_true", help="Simulate backup execution")

    # list
    subparsers.add_parser("list", parents=[common_parser], help="List all configured jobs")

    # backups
    p_backups = subparsers.add_parser("backups", parents=[common_parser], help="List existing backup archives on disk and restore commands")
    p_backups.add_argument("job", nargs="?", default=None, help="Optional job name filter")

    # validate
    subparsers.add_parser("validate", parents=[common_parser], help="Validate YAML config files in jobs directory")

    # init
    p_init = subparsers.add_parser("init", parents=[common_parser], help="Initialize starter template configuration files into a directory")
    p_init.add_argument("dest", nargs="?", default=".", help="Target directory to initialize templates into (default: current directory)")
    p_init.add_argument("--force", "-f", action="store_true", help="Overwrite existing files in target directory")

    args = parser.parse_args()

    if not args.subcommand or args.subcommand == "tui":
        sys.exit(cmd_tui(args))
    elif args.subcommand == "daemon":
        sys.exit(cmd_daemon(args))
    elif args.subcommand == "run":
        sys.exit(cmd_run(args))
    elif args.subcommand == "list":
        sys.exit(cmd_list(args))
    elif args.subcommand == "backups":
        sys.exit(cmd_backups(args))
    elif args.subcommand == "validate":
        sys.exit(cmd_validate(args))
    elif args.subcommand == "init":
        sys.exit(cmd_init(args))
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
