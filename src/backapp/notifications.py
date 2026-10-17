"""Webhook notification dispatcher (Discord, Slack, and generic webhooks) for backapp."""

from __future__ import annotations

import json
import logging
import os
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger("backapp.notifications")


@dataclass
class WebhookConfig:
    url: Optional[str] = None
    on_start: bool = True
    on_success: bool = True
    on_failure: bool = True

    @classmethod
    def from_dict(cls, data: Optional[Dict[str, Any]]) -> WebhookConfig:
        if not data:
            # Fallback to global environment variable if present
            env_url = os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("BACKAPP_WEBHOOK_URL")
            return cls(url=env_url) if env_url else cls(url=None)

        if isinstance(data, str):
            return cls(url=data)

        url = data.get("discord_webhook") or data.get("url") or data.get("webhook_url")
        if not url:
            url = os.environ.get("DISCORD_WEBHOOK_URL") or os.environ.get("BACKAPP_WEBHOOK_URL")

        return cls(
            url=url,
            on_start=bool(data.get("on_start", True)),
            on_success=bool(data.get("on_success", True)),
            on_failure=bool(data.get("on_failure", True)),
        )

    def resolve_url(self) -> Optional[str]:
        if not self.url:
            return None
        return os.path.expandvars(self.url.strip())


def _post_json(url: str, payload: Dict[str, Any], timeout: float = 8.0) -> bool:
    """Send JSON payload via HTTP POST using standard library."""
    try:
        data = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=data,
            headers={
                "User-Agent": "backapp-daemon/1.0",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=timeout) as response:
            return response.status in (200, 204)
    except Exception as e:
        logger.warning("Failed to send webhook notification to %s: %s", url, e)
        return False


def notify_backup_started(job_name: str, job_type: str, output_file: Path, webhook_cfg: WebhookConfig) -> None:
    """Send notification when a backup starts."""
    if not webhook_cfg.on_start:
        return
    url = webhook_cfg.resolve_url()
    if not url:
        return

    is_discord = "discord.com" in url or "discordapp.com" in url

    if is_discord:
        payload = {
            "username": "backapp Daemon",
            "avatar_url": "https://raw.githubusercontent.com/itzg/docker-minecraft-server/master/docs/logo.png",
            "embeds": [
                {
                    "title": f"🚀 Backup Started: `{job_name}`",
                    "description": f"Starting backup process for job **{job_name}**.",
                    "color": 0x3498DB,  # Blue
                    "fields": [
                        {"name": "Archetype / Type", "value": job_type.upper(), "inline": True},
                        {"name": "Target File", "value": f"`{output_file.name}`", "inline": True},
                        {"name": "Output Directory", "value": str(output_file.parent), "inline": False},
                    ],
                    "footer": {"text": "backapp • Zero-Impact Production Backup Engine"},
                }
            ],
        }
    else:
        payload = {
            "text": f"🚀 [backapp] Backup started for job '{job_name}' (Target: {output_file.name})",
            "job": job_name,
            "type": job_type,
            "event": "started",
            "output_file": str(output_file),
        }

    logger.info("Sending backup started notification for '%s' to webhook...", job_name)
    _post_json(url, payload)


def notify_backup_completed(
    job_name: str,
    job_type: str,
    output_file: Path,
    success: bool,
    duration_seconds: float,
    file_size_formatted: str,
    webhook_cfg: WebhookConfig,
    pruned_count: int = 0,
    error_message: Optional[str] = None,
) -> None:
    """Send notification when a backup finishes (success or failure)."""
    if success and not webhook_cfg.on_success:
        return
    if not success and not webhook_cfg.on_failure:
        return

    url = webhook_cfg.resolve_url()
    if not url:
        return

    is_discord = "discord.com" in url or "discordapp.com" in url

    if success:
        if is_discord:
            fields = [
                {"name": "Archive Name", "value": f"`{output_file.name}`", "inline": True},
                {"name": "Archive Size", "value": file_size_formatted, "inline": True},
                {"name": "Duration", "value": f"{duration_seconds:.2f}s", "inline": True},
            ]
            if pruned_count > 0:
                fields.append({"name": "Retention", "value": f"Pruned {pruned_count} old archive(s)", "inline": True})

            payload = {
                "username": "backapp Daemon",
                "embeds": [
                    {
                        "title": f"✅ Backup Succeeded: `{job_name}`",
                        "description": f"Successfully backed up **{job_name}** without server impact.",
                        "color": 0x2ECC71,  # Green
                        "fields": fields,
                        "footer": {"text": "backapp • Zero-Impact Production Backup Engine"},
                    }
                ],
            }
        else:
            payload = {
                "text": f"✅ [backapp] Backup succeeded for '{job_name}'! Size: {file_size_formatted}, Duration: {duration_seconds:.2f}s",
                "job": job_name,
                "status": "success",
                "size": file_size_formatted,
                "duration": duration_seconds,
                "archive": str(output_file),
                "pruned": pruned_count,
            }
    else:
        if is_discord:
            payload = {
                "username": "backapp Daemon",
                "embeds": [
                    {
                        "title": f"❌ Backup Failed: `{job_name}`",
                        "description": f"Backup for **{job_name}** failed!",
                        "color": 0xE74C3C,  # Red
                        "fields": [
                            {"name": "Error Details", "value": f"```{error_message or 'Unknown error'}```", "inline": False},
                            {"name": "Duration", "value": f"{duration_seconds:.2f}s", "inline": True},
                        ],
                        "footer": {"text": "backapp • Zero-Impact Production Backup Engine"},
                    }
                ],
            }
        else:
            payload = {
                "text": f"❌ [backapp] Backup FAILED for '{job_name}': {error_message}",
                "job": job_name,
                "status": "failed",
                "error": error_message,
                "duration": duration_seconds,
            }

    status_str = "succeeded" if success else "failed"
    logger.info("Sending backup %s notification for '%s' to webhook...", status_str, job_name)
    _post_json(url, payload)
