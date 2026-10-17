"""Tests for webhook notifications (Discord & generic)."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch
from backapp.notifications import WebhookConfig, notify_backup_completed, notify_backup_started


def test_webhook_config_resolution():
    cfg = WebhookConfig(url="https://discord.com/api/webhooks/123/xyz")
    assert cfg.resolve_url() == "https://discord.com/api/webhooks/123/xyz"
    assert cfg.on_start is True
    assert cfg.on_success is True


@patch("urllib.request.urlopen")
def test_discord_start_notification(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    cfg = WebhookConfig(url="https://discord.com/api/webhooks/123/xyz", on_start=True)
    notify_backup_started("mc_survival", "minecraft", Path("/backups/mc.tar.zst"), cfg)

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert "embeds" in payload
    assert "Backup Started: `mc_survival`" in payload["embeds"][0]["title"]


@patch("urllib.request.urlopen")
def test_discord_success_notification(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    cfg = WebhookConfig(url="https://discord.com/api/webhooks/123/xyz", on_success=True)
    notify_backup_completed(
        job_name="mc_survival",
        job_type="minecraft",
        output_file=Path("/backups/mc.tar.zst"),
        success=True,
        duration_seconds=3.42,
        file_size_formatted="1.2 GB",
        webhook_cfg=cfg,
        pruned_count=2,
    )

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert "embeds" in payload
    assert "Backup Succeeded: `mc_survival`" in payload["embeds"][0]["title"]
    fields = {f["name"]: f["value"] for f in payload["embeds"][0]["fields"]}
    assert fields["Archive Size"] == "1.2 GB"
    assert "1.2 GB" in fields["Archive Size"]
    assert "Pruned 2" in fields["Retention"]


@patch("urllib.request.urlopen")
def test_discord_failure_notification(mock_urlopen):
    mock_resp = MagicMock()
    mock_resp.status = 204
    mock_urlopen.return_value.__enter__.return_value = mock_resp

    cfg = WebhookConfig(url="https://discord.com/api/webhooks/123/xyz", on_failure=True)
    notify_backup_completed(
        job_name="pg_main",
        job_type="postgres",
        output_file=Path("/backups/pg.dump.zst"),
        success=False,
        duration_seconds=1.2,
        file_size_formatted="0 B",
        webhook_cfg=cfg,
        error_message="pg_dump connection refused",
    )

    assert mock_urlopen.called
    req = mock_urlopen.call_args[0][0]
    payload = json.loads(req.data.decode("utf-8"))
    assert "embeds" in payload
    assert "Backup Failed: `pg_main`" in payload["embeds"][0]["title"]
    assert "pg_dump connection refused" in payload["embeds"][0]["fields"][0]["value"]
