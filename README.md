# 🛡️ backapp

**A production-ready, zero-impact backup daemon and interactive TUI for multi-server environments (Minecraft, PostgreSQL, MySQL) packaged in Docker and published to GHCR.**

Designed specifically for production servers, game servers, and database clusters where game tick latency and database transaction throughput must never be compromised by background backups.

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

### 2. Multi-Server Centralized Architecture
- Backs up multiple servers concurrently from a single lightweight container:
  - **Server 1**: Minecraft Survival Server (`mc_survival`)
  - **Server 2**: Minecraft Creative / Lobby Server (`mc_creative`)
  - **Server 3**: PostgreSQL Production Database (`postgres_db`)
  - **Server 4**: MySQL Database (`mysql_db`)
- Reads server volumes read-only (`:ro`) and triggers remote RCON / dump hooks across the internal Docker network.

### 3. Pre-built GHCR Container Image
- Ready to pull from GitHub Container Registry:
  ```bash
  docker pull ghcr.io/<owner>/backapp:latest
  ```
- Automated multi-arch build (`linux/amd64`, `linux/arm64`) with GitHub Actions workflow in `.github/workflows/docker-publish.yml`.

### 4. Immediate "Backup Now" Execution
- Trigger backups immediately without waiting for cron schedules:
  ```bash
  # 1. Using the convenient root helper script:
  ./backup-now.sh mc_survival   # Backup single server now
  ./backup-now.sh --all         # Backup ALL servers now

  # 2. Directly via Docker Compose:
  docker compose exec backapp backapp run mc_survival
  docker compose exec backapp backapp run --all

  # 3. Via Interactive TUI:
  # Press 'r' on any job in the TUI table
  ```

### 5. Discord & Webhook Notifications
- Real-time notifications when a backup **starts** and when it **completes** (success or failure).
- **Discord Rich Embeds**:
  - 🚀 **Started**: Blue embed displaying the job archetype, target filename, and output directory.
  - ✅ **Succeeded**: Green embed displaying duration, formatted size, and retention pruned counts.
  - ❌ **Failed**: Red alert embed displaying the error message, duration, and hook diagnostics.
- Works with Discord, Slack, and generic JSON webhook endpoints.

### 6. Grandfather-Father-Son (GFS) Retention Rotation
- In addition to standard `keep_last: N`, `backapp` natively supports **GFS retention**:
  - **Son (Daily)**: Keep 1 snapshot per day for the last $D$ days (`keep_daily`).
  - **Father (Weekly)**: Keep 1 snapshot per week for the last $W$ weeks (`keep_weekly`).
  - **Grandfather (Monthly)**: Keep 1 snapshot per month for the last $M$ months (`keep_monthly`).
  - **Great-Grandfather (Yearly)**: Keep 1 snapshot per year for the last $Y$ years (`keep_yearly`).

### 7. Descriptive Schedule Syntax
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

---

## 🚀 Quick Start with Docker Compose

### 1. Configure and Start Multi-Server Environment

In `docker-compose.yml`, specify your GHCR image or build locally:
```yaml
services:
  backapp:
    image: ghcr.io/<owner>/backapp:latest
    # or build locally:
    build: .
```

Start the stack:
```bash
# Set your Discord webhook (optional)
export DISCORD_WEBHOOK_URL="https://discord.com/api/webhooks/YOUR_WEBHOOK_URL"

docker compose up -d
```

### 2. Run a Backup Now

```bash
# Run backup for a specific server immediately
./backup-now.sh mc_survival

# Or run backups for ALL configured servers immediately
./backup-now.sh --all
```

### 3. Launch the Interactive TUI

Connect into the container to open the interactive dashboard:
```bash
docker compose exec -it backapp backapp tui
```
**TUI Keybindings:**
- **`[R]`**: Run selected backup now
- **`[B]`**: View archives and one-liner restore command
- **`[L]`**: View real-time logs
- **`[C]`**: Reload job configs without restarting daemon
- **`[Q]`**: Quit

---

## 📋 Multi-Server Configuration Specs

Place YAML configuration files inside `/etc/backup/jobs/`:

### 1. Server 1: Minecraft Survival (`mc_survival.yaml`)
```yaml
name: "mc_survival"
schedule: "0 */4 * * *" # Every 4 hours
format: "tar.zst"
source_dir: "/data/servers/mc_survival"
output_dir: "/mnt/backups/minecraft"

retention:
  keep_last: 10
  gfs:
    keep_daily: 7
    keep_weekly: 4
    keep_monthly: 12
    keep_yearly: 1

notifications:
  discord_webhook: "$DISCORD_WEBHOOK_URL"

hooks:
  pre:
    - 'rcon-cli -H mc_survival -P 25575 -p "$RCON_PASS" "save-off"'
    - 'rcon-cli -H mc_survival -P 25575 -p "$RCON_PASS" "save-all flush"'
  post:
    - 'rcon-cli -H mc_survival -P 25575 -p "$RCON_PASS" "save-on"'
```

### 2. Server 2: Minecraft Creative / Lobby (`mc_creative.yaml`)
```yaml
name: "mc_creative"
schedule: "0 */6 * * *"
format: "tar.zst"
source_dir: "/data/servers/mc_creative"
output_dir: "/mnt/backups/mc_creative"

retention:
  keep_last: 8
  gfs:
    keep_daily: 7
    keep_weekly: 4

notifications:
  discord_webhook: "$DISCORD_WEBHOOK_URL"

hooks:
  pre:
    - 'rcon-cli -H mc_creative -P 25575 -p "$RCON_PASS" "save-off"'
    - 'rcon-cli -H mc_creative -P 25575 -p "$RCON_PASS" "save-all flush"'
  post:
    - 'rcon-cli -H mc_creative -P 25575 -p "$RCON_PASS" "save-on"'
```

### 3. Server 3: PostgreSQL Database (`pg_main.yaml`)
```yaml
name: "pg_main"
schedule: "0 2 * * *" # Daily at 02:00 AM
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

command: "pg_dump -h postgres_db -U postgres -d prod_db -Fc"
output_filename: "prod_db_{timestamp}.dump.zst"
```

### 4. Server 4: MySQL Database (`mysql_main.yaml`)
```yaml
name: "mysql_main"
schedule: "0 3 * * *"
type: "mysql"
host: "mysql_db"
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

# Trigger immediate backups for ALL configured jobs
backapp run --all

# List backup archives on disk and their restore commands
backapp backups mc_survival

# Validate YAML syntax, schedule, webhooks, and GFS settings
backapp validate --jobs-dir /etc/backup/jobs
```

---

## 📄 License
MIT License.
