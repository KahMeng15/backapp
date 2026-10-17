"""Interactive Terminal User Interface (TUI) for backmc using Textual."""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    DataTable,
    Footer,
    Header,
    Label,
    RichLog,
    Static,
)

from backmc.config import JobConfig, load_jobs_from_directory
from backmc.engine import BackupEngine, format_bytes, get_restore_command
from backmc.ipc import IPCClient, resolve_socket_path
from backmc.retention import list_job_backups


class BackupsModal(ModalScreen[None]):
    """Modal screen displaying list of backups for a job and one-liner restore commands."""

    def __init__(self, job_name: str, backups: List[Dict[str, Any]]):
        super().__init__()
        self.job_name = job_name
        self.backups = backups

    def compose(self) -> ComposeResult:
        with Vertical(id="backups-dialog"):
            yield Label(f"📦 Backup Archives for '{self.job_name}'", id="dialog-title")
            yield Label("Select an archive to view its direct one-liner restore command.", id="dialog-subtitle")
            
            table = DataTable(id="backups-table", zebra_stripes=True)
            table.cursor_type = "row"
            yield table

            yield Static(id="restore-box", content="Select an archive above to view restore command...")
            
            with Horizontal(id="dialog-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-btn")

    def on_mount(self) -> None:
        table = self.query_one("#backups-table", DataTable)
        table.add_columns("Filename", "Size", "Created Date")
        
        for b in self.backups:
            table.add_row(
                b.get("filename", ""),
                b.get("size_formatted", format_bytes(b.get("size_bytes", 0))),
                b.get("mtime", ""),
                key=b.get("filename", ""),
            )

        if self.backups:
            self._update_restore_box(0)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        row_idx = event.cursor_row
        self._update_restore_box(row_idx)

    def _update_restore_box(self, row_idx: int) -> None:
        if 0 <= row_idx < len(self.backups):
            b = self.backups[row_idx]
            cmd = b.get("restore_command", "")
            path = b.get("path", "")
            txt = (
                f"[bold cyan]Selected Archive:[/bold cyan] {path}\n"
                f"[bold green]Direct Restore One-Liner (No custom tool required):[/bold green]\n"
                f"[bold white on #1a1a2e]  $ {cmd}  [/bold white on #1a1a2e]"
            )
            self.query_one("#restore-box", Static).update(txt)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-btn":
            self.dismiss()

    def key_escape(self) -> None:
        self.dismiss()


class LogsModal(ModalScreen[None]):
    """Modal screen displaying recent daemon / backup logs."""

    def __init__(self, logs: List[Dict[str, Any]]):
        super().__init__()
        self.logs = logs

    def compose(self) -> ComposeResult:
        with Vertical(id="logs-dialog"):
            yield Label("📜 backmc Operations & Backup Logs", id="logs-title")
            rich_log = RichLog(id="logs-view", highlight=True, markup=True)
            yield rich_log
            with Horizontal(id="logs-buttons"):
                yield Button("Close (Esc)", variant="primary", id="close-logs-btn")

    def on_mount(self) -> None:
        rich_log = self.query_one("#logs-view", RichLog)
        if not self.logs:
            rich_log.write("[dim]No recent logs recorded yet.[/dim]")
            return

        for entry in self.logs:
            lvl = entry.get("level", "INFO")
            ts = entry.get("timestamp", "")
            msg = entry.get("message", "")
            color = "green" if lvl == "INFO" else "yellow" if lvl == "WARNING" else "red" if lvl == "ERROR" else "cyan"
            rich_log.write(f"[dim]{ts}[/dim] [{color}][{lvl}][/{color}] {msg}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "close-logs-btn":
            self.dismiss()

    def key_escape(self) -> None:
        self.dismiss()


class BackmcApp(App[None]):
    """Main Textual interactive application for backmc."""

    CSS = """
    Screen {
        background: #0f111a;
        color: #e2e8f0;
    }

    #header-container {
        dock: top;
        height: auto;
        padding: 1 2;
        background: #1a1d2d;
        border-bottom: heavy #38bdf8;
    }

    #app-title {
        text-style: bold;
        color: #38bdf8;
    }

    #daemon-status {
        color: #94a3b8;
    }

    #table-container {
        padding: 1 2;
        height: 1fr;
    }

    DataTable {
        height: 1fr;
        border: solid #2a2e45;
        background: #141724;
    }

    DataTable > .datatable--header {
        background: #1e2238;
        color: #38bdf8;
        text-style: bold;
    }

    DataTable > .datatable--cursor {
        background: #2563eb;
        color: #ffffff;
    }

    #actions-container {
        dock: bottom;
        height: auto;
        padding: 1 2;
        background: #1a1d2d;
        border-top: solid #2a2e45;
    }

    Button {
        margin-right: 1;
        min-width: 14;
    }

    /* Modals */
    BackupsModal, LogsModal {
        align: center middle;
    }

    #backups-dialog, #logs-dialog {
        padding: 1 2;
        width: 85%;
        height: 80%;
        background: #1a1d2d;
        border: thick #38bdf8;
    }

    #dialog-title, #logs-title {
        text-style: bold;
        color: #38bdf8;
        margin-bottom: 1;
    }

    #dialog-subtitle {
        color: #94a3b8;
        margin-bottom: 1;
    }

    #restore-box {
        margin-top: 1;
        padding: 1;
        background: #0f111a;
        border: solid #334155;
        height: auto;
    }

    #dialog-buttons, #logs-buttons {
        margin-top: 1;
        align: right middle;
        height: auto;
    }

    #logs-view {
        height: 1fr;
        background: #0f111a;
        border: solid #334155;
        padding: 1;
    }
    """

    BINDINGS = [
        Binding("q", "quit", "Quit", show=True),
        Binding("r", "run_backup", "Run Now", show=True),
        Binding("b", "view_backups", "View Archives", show=True),
        Binding("l", "view_logs", "View Logs", show=True),
        Binding("c", "reload_configs", "Reload Jobs", show=True),
    ]

    def __init__(
        self,
        jobs_dir: Path = Path("/etc/backup/jobs"),
        socket_path: Optional[Path] = None,
    ):
        super().__init__()
        self.jobs_dir = jobs_dir
        self.socket_path = socket_path or resolve_socket_path()
        self.ipc = IPCClient(self.socket_path)
        self.local_jobs: Dict[str, JobConfig] = {}
        self.cached_status: Dict[str, Any] = {}

    def compose(self) -> ComposeResult:
        yield Header(show_clock=True)
        with Vertical(id="header-container"):
            yield Label("BACKMC - Zero-Impact Backup Engine & Daemon", id="app-title")
            yield Label("Connecting to daemon...", id="daemon-status")

        with Container(id="table-container"):
            table = DataTable(id="jobs-table", zebra_stripes=True)
            table.cursor_type = "row"
            yield table

        with Horizontal(id="actions-container"):
            yield Button("▶ Run Job (R)", variant="success", id="btn-run")
            yield Button("📦 Archives (B)", variant="primary", id="btn-backups")
            yield Button("📜 Logs (L)", variant="default", id="btn-logs")
            yield Button("🔄 Reload (C)", variant="warning", id="btn-reload")
            yield Button("✕ Quit (Q)", variant="error", id="btn-quit")

        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one("#jobs-table", DataTable)
        table.add_columns(
            "Job Name",
            "Type",
            "Schedule",
            "Status",
            "Next Run",
            "Last Run",
            "Duration",
            "Latest Backup",
            "Keep",
        )
        self.refresh_data()
        self.set_interval(2.0, self.refresh_data)

    def refresh_data(self) -> None:
        """Fetch latest state from daemon (or local disk) and update the table."""
        is_daemon = self.ipc.is_daemon_running()
        status_lbl = self.query_one("#daemon-status", Label)

        if is_daemon:
            resp = self.ipc.send_request({"action": "status"})
            if resp.get("status") == "ok":
                daemon_info = resp.get("daemon", {})
                pid = daemon_info.get("pid")
                rss = daemon_info.get("rss_mb", 0.0)
                running = daemon_info.get("running_jobs", [])
                
                run_text = f" | Running: {len(running)}" if running else ""
                status_lbl.update(
                    f"[bold green]● DAEMON ACTIVE[/bold green] (PID {pid}, RAM RSS: {rss} MB, Low Priority Wrapped: nice -n 19 ionice -c 3{run_text})"
                )
                self.cached_status = resp.get("data", {})
                self._update_table_from_state(self.cached_status.get("jobs", {}))
                return

        # Fallback to direct inspection mode if daemon is offline
        status_lbl.update(
            "[bold yellow]○ STANDALONE MODE[/bold yellow] (Daemon offline. Inspecting job configurations directly from disk)"
        )
        try:
            self.local_jobs = load_jobs_from_directory(self.jobs_dir)
        except Exception:
            self.local_jobs = {}

        table = self.query_one("#jobs-table", DataTable)
        table.clear()
        for name, job in self.local_jobs.items():
            backups = list_job_backups(job)
            latest = backups[0].name if backups else "None"
            table.add_row(
                f"[bold cyan]{job.name}[/bold cyan]",
                job.type,
                job.schedule or "Manual",
                "[dim]STANDBY[/dim]",
                "Unknown (Daemon off)",
                "-",
                "-",
                latest,
                str(job.retention.keep_last),
                key=job.name,
            )

    def _update_table_from_state(self, jobs_dict: Dict[str, Any]) -> None:
        table = self.query_one("#jobs-table", DataTable)
        saved_row_idx = table.cursor_row
        table.clear()

        for name, data in jobs_dict.items():
            status = data.get("status", "IDLE")
            if status == "RUNNING":
                status_fmt = "[bold yellow]⏳ RUNNING[/bold yellow]"
            elif status == "SUCCESS":
                status_fmt = "[bold green]✔ SUCCESS[/bold green]"
            elif status == "FAILED":
                status_fmt = "[bold red]✖ FAILED[/bold red]"
            else:
                status_fmt = "[dim]IDLE[/dim]"

            dur = data.get("last_run_duration")
            dur_str = f"{dur}s" if dur is not None else "-"
            
            size = data.get("last_backup_size")
            latest_file = data.get("last_backup_file") or "-"
            if size is not None and size > 0:
                latest_display = f"{latest_file} ({format_bytes(size)})"
            else:
                latest_display = latest_file

            table.add_row(
                f"[bold cyan]{name}[/bold cyan]",
                data.get("job_type", "filesystem"),
                data.get("schedule") or "Manual",
                status_fmt,
                data.get("next_run_time") or "-",
                data.get("last_run_time") or "-",
                dur_str,
                latest_display,
                str(data.get("keep_last", 10)),
                key=name,
            )

        # Restore cursor position if valid
        if saved_row_idx is not None and saved_row_idx < table.row_count:
            table.cursor_coordinate = (saved_row_idx, 0)

    def get_selected_job_name(self) -> Optional[str]:
        table = self.query_one("#jobs-table", DataTable)
        if table.row_count == 0 or table.cursor_row is None:
            return None
        row_key = table.coordinate_to_cell_key((table.cursor_row, 0)).row_key.value
        return str(row_key)

    def action_run_backup(self) -> None:
        job_name = self.get_selected_job_name()
        if not job_name:
            self.notify("Please select a job from the table first.", severity="warning")
            return

        if self.ipc.is_daemon_running():
            resp = self.ipc.send_request({"action": "trigger", "job_name": job_name})
            if resp.get("status") == "ok":
                self.notify(f"Triggered backup for '{job_name}'!", severity="information")
                self.refresh_data()
            else:
                self.notify(f"Failed: {resp.get('message')}", severity="error")
        else:
            # Standalone execution
            if job_name in self.local_jobs:
                self.notify(f"Running '{job_name}' directly...", severity="information")
                engine = BackupEngine()
                res = engine.execute_job(self.local_jobs[job_name])
                if res.success:
                    self.notify(f"Backup succeeded: {res.output_file.name}", severity="information")
                else:
                    self.notify(f"Backup failed: {res.error_message}", severity="error")
                self.refresh_data()

    def action_view_backups(self) -> None:
        job_name = self.get_selected_job_name()
        if not job_name:
            self.notify("Please select a job first.", severity="warning")
            return

        backups_list: List[Dict[str, Any]] = []
        if self.ipc.is_daemon_running():
            resp = self.ipc.send_request({"action": "list_backups", "job_name": job_name})
            if resp.get("status") == "ok":
                backups_list = resp.get("backups", [])
        else:
            if job_name in self.local_jobs:
                files = list_job_backups(self.local_jobs[job_name])
                for f in files:
                    stat = f.stat()
                    backups_list.append({
                        "filename": f.name,
                        "path": str(f),
                        "size_bytes": stat.st_size,
                        "size_formatted": format_bytes(stat.st_size),
                        "mtime": datetime.datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                        "restore_command": get_restore_command(f),
                    })

        self.push_screen(BackupsModal(job_name, backups_list))

    def action_view_logs(self) -> None:
        logs_list: List[Dict[str, Any]] = []
        if self.ipc.is_daemon_running():
            resp = self.ipc.send_request({"action": "logs", "limit": 100})
            if resp.get("status") == "ok":
                logs_list = resp.get("logs", [])
        self.push_screen(LogsModal(logs_list))

    def action_reload_configs(self) -> None:
        if self.ipc.is_daemon_running():
            resp = self.ipc.send_request({"action": "reload"})
            self.notify(resp.get("message", "Reloaded"), severity="information")
        else:
            self.refresh_data()
            self.notify("Reloaded local job configs from disk.", severity="information")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        btn_id = event.button.id
        if btn_id == "btn-run":
            self.action_run_backup()
        elif btn_id == "btn-backups":
            self.action_view_backups()
        elif btn_id == "btn-logs":
            self.action_view_logs()
        elif btn_id == "btn-reload":
            self.action_reload_configs()
        elif btn_id == "btn-quit":
            self.exit()
