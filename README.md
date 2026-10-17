# 🛡️ backmc

**A production-ready, zero-impact backup daemon and interactive TUI for Minecraft, PostgreSQL, and MySQL packaged in Docker.**

Designed specifically for production servers and game servers where game tick latency and database transaction throughput must never be degraded by background backups.

---

## 🌟 Key Features

### 1. Zero-Impact & Ultra-Low Resource Footprint
- **0.0% CPU When Idle**: Uses an asynchronous event scheduler powered by `croniter` that sleeps precisely until the next job is due.
- **Minimal RAM Footprint (~18–25 MB RSS)**: The background daemon runs with virtually no memory overhead.
- **Priority Throttling**: All archive, compression, and database dump subprocesses are wrapped with:
  ```bash
  nice -n 19 ionice -c 3 <command>
  ```
  This places CPU scheduling in the lowest priority and I/O scheduling in the idle class (`IDLE`), guaranteeing zero lag or stuttering on active Minecraft servers and production databases.
- **Zero Python RAM Buffering**: Backup data is streamed **directly between kernel subprocess pipes** (e.g. `tar -cf - . | zstd -T0 -3 > output.tmp`). No archive chunks or database bytes are ever loaded into Python memory.

### 2. Direct File Restoration (No Custom or Proprietary Formats)
- Every backup archive is saved in standard, portable formats:
  - `.tar.zst` (Zstandard compressed tarball)
  - `.tar.gz` (Gzip compressed tarball)
  - `.zip` (Standard Zip archive)
  - `.dump.zst` / `.sql.zst` (PostgreSQL / MySQL dumps compressed with Zstandard)
- Naming format: `<job_name>_<YYYY-MM-DD_HH-MM-SS>.<ext>`
- **Can be restored on any computer without this tool installed.**

### 3. Native Archetype Support
- **Filesystem / Minecraft**:
  - Executes pre-hooks (e.g. `rcon-cli ... "save-off"` and `rcon-cli ... "save-all flush"`).
  - Streams source directory to `.tar.zst`, `.tar.gz`, or `.zip`.
  - Executes post-hooks (e.g. `rcon-cli ... "save-on"`) inside a **guaranteed `finally:` block**—so Minecraft `save-on` is restored even if backup or pre-hooks fail.
- **PostgreSQL**:
  - Streams `pg_dump` directly to a compressed file on target disk.
  - Supports custom format (`-Fc`) or plain SQL streams.
- **MySQL / MariaDB**:
  - Streams `mysqldump --single-transaction --quick` directly to a compressed file.
- **Generic Command Streaming**:
  - Direct shell command piping to compressor for custom backup pipelines.

### 4. Interactive Terminal UI (TUI) & Daemon IPC
- Built with **Textual**:
  - Live table of all jobs with schedule, status, duration, and latest backup size.
  - Connects to the running daemon via lightweight UNIX domain socket (`/var/run/backmc/backmc.sock`).
  - **[R] Run Job Now**: Trigger immediate backup of selected job.
  - **[B] View Archives**: Inspect existing backup archives and immediately view the exact one-liner restore command.
  - **[L] Live Logs**: Inspect daemon execution logs and hook outputs in real-time.
  - **[C] Reload**: Hot-reload job configurations without restarting the daemon.
  - Can also run in standalone/direct inspection mode if the daemon is offline.

---

## 🚀 Quick Start with Docker

### 1. Start the Daemon with Docker Compose

Clone and start the service:
```bash
docker compose up -d
```

Check daemon logs:
```bash
docker compose logs -f backmc
```

### 2. Launch the Interactive TUI

Connect into the running container to access the full interactive dashboard:
```bash
docker compose exec -it backmc backmc tui
```

---

## 📋 Job Configuration Specs

Place YAML configuration files inside `/etc/backup/jobs/` (or specify `--jobs-dir`):

### 1. Minecraft Survival Server (`mc_survival.yaml`)
```yaml
# /etc/backup/jobs/mc_survival.yaml
name: "mc_survival"
schedule: "0 */4 * * *" # Every 4 hours
format: "tar.zst"       # tar.zst, tar.gz, zip
source_dir: "/data/minecraft_world"
output_dir: "/mnt/backups/minecraft"
retention:
  keep_last: 10
hooks:
  pre:
    - 'rcon-cli -H mc_srv -P 25575 -p "$RCON_PASS" "save-off"'
    - 'rcon-cli -H mc_srv -P 25575 -p "$RCON_PASS" "save-all flush"'
  post:
    - 'rcon-cli -H mc_srv -P 25575 -p "$RCON_PASS" "save-on"'
exclude:
  - "*.lock"
  - "cache/*"
```

### 2. PostgreSQL Production Database (`pg_main.yaml`)
```yaml
# /etc/backup/jobs/pg_main.yaml
name: "pg_main"
schedule: "0 2 * * *" # Daily at 2:00 AM
format: "dump.zst"
output_dir: "/mnt/backups/postgres"
retention:
  keep_last: 7
command: "pg_dump -h pg_srv -U postgres -d prod_db -Fc"
output_filename: "prod_db_{timestamp}.dump.zst"
```

### 3. MySQL / MariaDB Database (`mysql_main.yaml`)
```yaml
# /etc/backup/jobs/mysql_main.yaml
name: "mysql_main"
schedule: "0 3 * * *" # Daily at 3:00 AM
type: "mysql"
host: "mysql_srv"
port: 3306
user: "root"
password_env: "MYSQL_PWD"
database: "production_db"
format: "sql.zst"
output_dir: "/mnt/backups/mysql"
retention:
  keep_last: 14
```

---

## 🛠️ Direct Restoration Guide (No Tool Required)

Restore any backup archive created by `backmc` using standard Linux/UNIX tools:

| Format | Restoration Command |
| :--- | :--- |
| **`.tar.zst`** | `zstd -d -c backup.tar.zst \| tar -xf - -C /target/dir` |
| **`.tar.gz`** | `tar -xzf backup.tar.gz -C /target/dir` |
| **`.zip`** | `unzip backup.zip -d /target/dir` |
| **`.dump.zst`** | `zstd -d -c prod_db.dump.zst \| pg_restore -d <database_name>` |
| **`.sql.zst`** | `zstd -d -c backup.sql.zst \| mysql -u root -p <database_name>` |

---

## 💻 CLI Commands

```bash
# Start background backup daemon
backmc daemon

# Launch interactive terminal UI
backmc tui

# List all configured jobs and status
backmc list

# Trigger an immediate backup of a specific job
backmc run mc_survival

# List backup archives on disk and their restore commands
backmc backups mc_survival

# Validate YAML syntax and configuration options
backmc validate --jobs-dir /etc/backup/jobs
```

---

## 🧪 Local Development & Testing

```bash
# Create and activate virtual environment
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies and package in editable mode
pip install -e .

# Run test suite
pytest -v
```

---

## 📄 License
MIT License.
