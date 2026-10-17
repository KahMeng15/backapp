"""Tests for hook execution and guaranteed cleanup."""

import os
import pytest
from backapp.hooks import HookExecutionError, run_post_hooks, run_pre_hooks, run_single_hook


def test_hook_env_expansion():
    os.environ["TEST_RCON_PASS"] = "secret123"
    proc = run_single_hook('echo "Password is $TEST_RCON_PASS"')
    assert proc.returncode == 0
    assert "Password is secret123" in proc.stdout


def test_pre_hook_failure():
    with pytest.raises(HookExecutionError):
        run_pre_hooks(["exit 1"])


def test_post_hooks_guaranteed_finally():
    # If the first post hook fails, subsequent hooks should STILL run!
    marker_file = "/tmp/backmc_post_hook_marker.txt"
    if os.path.exists(marker_file):
        os.unlink(marker_file)

    hooks = [
        "exit 2",  # Failing hook
        f"touch {marker_file}",  # Critical cleanup hook (e.g. save-on)
    ]
    errs = run_post_hooks(hooks)
    assert len(errs) == 1
    assert os.path.exists(marker_file)
    os.unlink(marker_file)


def test_rcon_command_resolution():
    from backapp.config import RconConfig
    from backapp.hooks import resolve_hook_command

    rcon = RconConfig(host="192.168.1.100", port=25575, password="$RCON_PASS")

    # Bare minecraft commands
    cmd1 = resolve_hook_command("save-off", rcon=rcon)
    assert cmd1 == 'rcon-cli -H 192.168.1.100 -P 25575 -p "$RCON_PASS" "save-off"'

    cmd2 = resolve_hook_command("save-all flush", rcon=rcon)
    assert cmd2 == 'rcon-cli -H 192.168.1.100 -P 25575 -p "$RCON_PASS" "save-all flush"'

    # Prefix rcon: or rcon
    cmd3 = resolve_hook_command("rcon: save-on", rcon=rcon)
    assert cmd3 == 'rcon-cli -H 192.168.1.100 -P 25575 -p "$RCON_PASS" "save-on"'

    cmd4 = resolve_hook_command("rcon say Hello Server", rcon=rcon)
    assert cmd4 == 'rcon-cli -H 192.168.1.100 -P 25575 -p "$RCON_PASS" "say Hello Server"'

    # Explicit shell command escape
    cmd5 = resolve_hook_command("shell: echo 'starting backup'", rcon=rcon)
    assert cmd5 == "echo 'starting backup'"

    # Scripts starting with ./ or / stay shell commands
    cmd6 = resolve_hook_command("./scripts/prepare.sh", rcon=rcon)
    assert cmd6 == "./scripts/prepare.sh"
