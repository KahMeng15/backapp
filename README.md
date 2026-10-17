# 🛡️ backapp

**A production-ready, zero-impact backup daemon and interactive TUI for Minecraft, PostgreSQL, and MySQL packaged in Docker.**

Designed specifically for production servers, game servers, and database hosts where game tick latency and database throughput must never be compromised by background backups.

---

## 🌟 Key Features

### 1. Zero-Impact & Ultra-Low Resource Footprint
- **0.0% CPU When Idle**: Asynchronous scheduler driven by `croniter` that sleeps precisely until the next job is due.
- **Minimal RAM Footprint (~20 MB RSS)**: Runs with minimal memory overhead.
- **Priority Throttling**: All external archive, compression, and database dump subprocesses run wrapped with:
  ```bash
  nice -n 19 ionice -c 3 <command>
  ```
  *(With automatic fallback to `nice -n 19` if `ionice` is unsupported or restricted).*
- **Zero Python RAM Buffering**: Streams backup payloads **directly between kernel subprocess pipes** (e.g. `tar -cf - . | zstd -T0 -3 > output.tmp`). No archive chunks or database bytes are ever loaded into Python memory buffers.

### 2. Discord & Webhook Notifications
- Sends real-time notifications when a backup **starts** and when it **completes** (success or failure).
- **Discord Rich Embeds**:
  - 🚀 **Started**: Blue embed displaying the job archetype, target filename, and output directory.
  - ✅ **Succeeded**: Green embed displaying duration, formatted size, and retention pruned counts.
  - ❌ **Failed**: Red alert embed displaying the error message, duration, and hook diagnostics.
- Works with Discord, Slack, and generic JSON webhook endpoints.

### 3. Grandfather-Father-Son (GFS) Retention Rotation
- In addition to standard `keep_last: N`, `backapp` natively supports **GFS retention**:
  - **Son (Daily)**: Keep 1 snapshot per day for the last $D$ days (`keep_daily`).
  - **Father (Weekly)**: Keep 1 snapshot per week for the last $W$ weeks (`keep_weekly`).
  - **Grandfather (Monthly)**: Keep 1 snapshot per month for the last $M$ months (`keep_monthly`).
  - **Great-Grandfather (Yearly)**: Keep 1 snapshot per year for the last $Y$ years (`keep_yearly`).
- Automatically cleans up stale temporary `.tmp` files from interrupted runs.

### 4. Descriptive Schedule Syntax
- Standard 5-part cron syntax with an inline visual guide:
  ```
  ┌───────────── Minute (0 - 59)
  │ ┌───────────── Hour (0 - 23)
  │ │ ┌───────────── Day of the Month (1 - 31)
  │ │ │ ┌───────────── Month of the Year (1 - 12)
  │ │ │ │ ┌───────────── Day of the Week (0 - 6, Sunday is 0)
  │ │ │ │ │
  0 */4 * * *   -> Every 4 hours on the hour
  ```
- **Friendly Shortcuts**:
  - `"@hourly"` (`0 * * * *`)
  - `"@daily"` or `"@midnight"` (`0 0 * * *`)
  - `"@weekly"` (`0 0 * * 0`)
  - `"@monthly"` (`0 0 1 * *`)
  - `"every 4 hours"` (`0 */4 * * *`)
  - `"every 30 mins"` (`*/30 * * * *`)

### 5. Direct File Restoration (No Custom or Proprietary Formats)
- Every backup archive is saved in standard, portable formats:
  - `.tar.zst` (Zstandard compressed tarball)
  - `.tar.gz` (Gzip compressed tarball)
  - `.zip` (Standard Zip archive)
  - `.dump.zst` / `.sql.zst` (PostgreSQL / MySQL dumps compressed with Zstandard)
- Naming format: `<job_name>_<YYYY-MM-DD_HH-MM-SS>.<ext>`
- **Can be restored on any computer without this tool installed.**

### 6. Interactive Terminal UI (TUI) & Daemon IPC
- Built with **Textual**:
  - Live table of all jobs with schedule, status, duration, and latest backup size.
  - Connects to the running daemon via lightweight UNIX domain socket (`/var/run/backapp/backapp.sock`).
  - **`[R]` Run Job Now**: Trigger immediate backup of selected job.
  - **`[B]` View Archives**: Inspect existing backup archives and immediately view the exact one-liner restore command.
  - **`[L]` Live Logs**: Inspect daemon execution logs and hook outputs in real-time.
  - **`[C]` Reload**: Hot-reload job configurations without restarting the daemon.
  - Operates in standalone inspection mode if the daemon is offline.

---

## 🚀 Quick Start with Docker

### 1. Start the Daemon with Docker Compose

```bash
# Set your Discord webhook (optional)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"

docker compose up -d
```

Check daemon logs:
```bash
docker compose logs -f backapp
```

### 2. Launch the Interactive TUI

Connect into the running container to access the full interactive dashboard:
```bash
docker compose exec -it backapp backapp tui
```

---

## 📋 Job Configuration Specs

Place YAML configuration files inside `/etc/backup/jobs/` (or specify `--jobs-dir`):

### 1. Minecraft Survival Server (`mc_survival.yaml`)
```yaml
# /etc/backup/jobs/mc_survival.yaml
name: "mc_survival"

# ┌───────────── Minute (0 - 59)
# │ ┌───────────── Hour (0 - 23)
# │ │ ┌───────────── Day of the Month (1 - 31)
# │ │ │ ┌───────────── Month of the Year (1 - 12)
# │ │ │ │ ┌───────────── Day of the Week (0 - 6)
# │ │ │ │ │
schedule: "0 */4 * * *" # Every 4 hours (or use: "every 4 hours")

format: "tar.zst"       # tar.zst, tar.gz, zip
source_dir: "/data/minecraft_world"
output_dir: "/mnt/backups/minecraft"

# GFS Retention (Grandfather-Father-Son)
retention:
  keep_last: 10         # Minimum number of recent backups to keep
  gfs:
    keep_hourly: 24     # 1 per hour for 24 hours
    keep_daily: 7       # Son: 1 per day for 7 days
    keep_weekly: 4      # Father: 1 per week for 4 weeks
    keep_monthly: 12    # Grandfather: 1 per month for 12 months
    keep_yearly: 1      # Great-Grandfather: 1 per year for 1 year

# Discord / Webhook Notifications
notifications:
  discord_webhook: "$DISCORD_WEBHOOK_URL"
  on_start: true
  on_success: true
  on_failure: true

# Pre/Post hooks (guaranteed finally execution for save-on)
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
schedule: "0 2 * * *" # Daily at 02:00 AM (or use: "@daily")
format: "dump.zst"
output_dir: "/mnt/backups/postgres"

retention:
  keep_last: 7
  gfs:
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 6

notifications:
  discord_webhook: "$DISCORD_WEBHOOK_URL"
  on_start: true
  on_success: true
  on_failure: true

command: "pg_dump -h pg_srv -U postgres -d prod_db -Fc"
output_filename: "prod_db_{timestamp}.dump.zst"
```

### 3. MySQL / MariaDB Database (`mysql_main.yaml`)
```yaml
# /etc/backup/jobs/mysql_main.yaml
name: "mysql_main"
schedule: "0 3 * * *" # Daily at 03:00 AM
type: "mysql"
host: "mysql_srv"
port: 3306
user: "root"
password_env: "MYSQL_ROOT_PASSWORD"
database: "production_db"
format: "sql.zst"
output_dir: "/mnt/backups/mysql"

retention:
  keep_last: 14
  gfs:
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 12

notifications:
  discord_webhook: "$DISCORD_WEBHOOK_URL"
  on_start: true
  on_success: true
  on_failure: true
```

---

## 🛠️ Direct Restoration Guide (No Tool Required)

Restore any backup archive created by `backapp` using standard Linux/UNIX tools:

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
backapp daemon

# Launch interactive terminal UI
backapp tui

# List all configured jobs, schedule, and retention mode
backapp list

# Trigger an immediate backup of a specific job
backapp run mc_survival

# List backup archives on disk and their restore commands
backapp backups mc_survival

# Validate YAML syntax, schedule, webhooks, and GFS settings
backapp validate --jobs-dir /etc/backup/jobs
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
