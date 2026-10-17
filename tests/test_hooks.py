"""Tests for hook execution and guaranteed cleanup."""

import os
import pytest
from backmc.hooks import HookExecutionError, run_post_hooks, run_pre_hooks, run_single_hook


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
