"""Hook execution with environment variable expansion and guaranteed finally blocks."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import List, Optional

logger = logging.getLogger("backapp.hooks")


class HookExecutionError(Exception):
    """Raised when a hook command returns a non-zero exit code."""


def run_single_hook(cmd_template: str, env: Optional[dict[str, str]] = None, timeout: int = 45) -> subprocess.CompletedProcess[str]:
    """Execute a single hook string with environment variable expansion."""
    actual_env = os.environ.copy()
    if env:
        actual_env.update(env)

    # Expand environment variables (e.g. $RCON_PASS, ${VAR})
    expanded_cmd = os.path.expandvars(cmd_template)
    logger.info("Executing hook: %s", expanded_cmd)

    try:
        proc = subprocess.run(
            expanded_cmd,
            shell=True,
            capture_output=True,
            text=True,
            env=actual_env,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as e:
        logger.error("Hook timed out after %d seconds: %s", timeout, expanded_cmd)
        raise HookExecutionError(f"Hook timed out after {timeout}s: {expanded_cmd}") from e
    except Exception as e:
        logger.error("Hook failed to execute '%s': %s", expanded_cmd, e)
        raise HookExecutionError(f"Hook failed to execute: {e}") from e

    if proc.returncode != 0:
        logger.error(
            "Hook exited with code %d. Stderr: %s. Stdout: %s",
            proc.returncode,
            proc.stderr.strip(),
            proc.stdout.strip(),
        )
        raise HookExecutionError(
            f"Hook '{expanded_cmd}' exited with code {proc.returncode}: {proc.stderr.strip()}"
        )

    if proc.stdout.strip():
        logger.debug("Hook stdout: %s", proc.stdout.strip())

    return proc


def run_pre_hooks(hooks: List[str], env: Optional[dict[str, str]] = None) -> None:
    """Execute pre-backup hooks in order. Fails immediately on first error."""
    for hook in hooks:
        if hook.strip():
            run_single_hook(hook, env=env)


def run_post_hooks(hooks: List[str], env: Optional[dict[str, str]] = None) -> List[Exception]:
    """Execute post-backup hooks in order inside a guaranteed cleanup flow.
    
    Returns list of exceptions if any hook failed, but continues executing
    all subsequent post hooks (e.g. ensuring 'save-on' is called even if an earlier hook failed).
    """
    errors: List[Exception] = []
    for hook in hooks:
        if hook.strip():
            try:
                run_single_hook(hook, env=env)
            except Exception as e:
                logger.error("Post-hook failed: %s", e)
                errors.append(e)
    return errors
