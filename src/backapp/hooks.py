"""Hook execution with environment variable expansion and guaranteed finally blocks."""

from __future__ import annotations

import logging
import os
import subprocess
from typing import TYPE_CHECKING, List, Optional

if TYPE_CHECKING:
    from backapp.config import RconConfig

logger = logging.getLogger("backapp.hooks")


class HookExecutionError(Exception):
    """Raised when a hook command returns a non-zero exit code."""


def resolve_hook_command(cmd_template: str, rcon: Optional[RconConfig] = None) -> str:
    """Format and resolve a hook command string.
    
    If rcon is configured, automatically wraps Minecraft commands (e.g. 'save-off',
    'save-all flush', 'save-on', or commands prefixed with 'rcon:') with the full
    rcon-cli invocation using the configured host, port, and password.
    """
    cmd = cmd_template.strip()
    if not cmd or not rcon:
        return cmd

    # Explicit shell command escape
    if cmd.startswith("shell:") or cmd.startswith("sh:"):
        return cmd.split(":", 1)[1].strip()

    # If it is already an explicit rcon-cli call with host flag, leave as is
    if cmd.startswith("rcon-cli") and ("-H" in cmd or "--host" in cmd):
        return cmd

    # If prefixed with 'rcon:' or 'rcon '
    if cmd.startswith("rcon:"):
        inner_cmd = cmd.split(":", 1)[1].strip()
    elif cmd.startswith("rcon "):
        inner_cmd = cmd.split(None, 1)[1].strip()
    elif cmd.startswith("rcon-cli "):
        inner_cmd = cmd.split(None, 1)[1].strip()
    else:
        # Check if it looks like a custom shell script or pipeline
        if cmd.startswith(("./", "/", "~")) or any(op in cmd for op in (" | ", " && ", " || ", " ; ", " > ")):
            return cmd
        inner_cmd = cmd

    # Strip existing surrounding quotes if present
    if (inner_cmd.startswith('"') and inner_cmd.endswith('"')) or (inner_cmd.startswith("'") and inner_cmd.endswith("'")):
        inner_cmd = inner_cmd[1:-1]

    # Build rcon-cli invocation
    parts = [f"rcon-cli -H {rcon.host} -P {rcon.port}"]
    if rcon.password:
        parts.append(f'-p "{rcon.password}"')
    parts.append(f'"{inner_cmd}"')
    return " ".join(parts)


def run_single_hook(
    cmd_template: str,
    env: Optional[dict[str, str]] = None,
    rcon: Optional[RconConfig] = None,
    timeout: int = 45,
) -> subprocess.CompletedProcess[str]:
    """Execute a single hook string with environment variable expansion and rcon resolution."""
    actual_env = os.environ.copy()
    if rcon:
        actual_env.setdefault("RCON_HOST", rcon.host)
        actual_env.setdefault("RCON_PORT", str(rcon.port))
        if rcon.password:
            actual_env.setdefault("RCON_PASSWORD", rcon.password)
            actual_env.setdefault("RCON_PASS", rcon.password)
    if env:
        actual_env.update(env)

    resolved_cmd = resolve_hook_command(cmd_template, rcon=rcon)
    expanded_cmd = os.path.expandvars(resolved_cmd)
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


def run_pre_hooks(
    hooks: List[str],
    env: Optional[dict[str, str]] = None,
    rcon: Optional[RconConfig] = None,
) -> None:
    """Execute pre-backup hooks in order. Fails immediately on first error."""
    for hook in hooks:
        if hook.strip():
            run_single_hook(hook, env=env, rcon=rcon)


def run_post_hooks(
    hooks: List[str],
    env: Optional[dict[str, str]] = None,
    rcon: Optional[RconConfig] = None,
) -> List[Exception]:
    """Execute post-backup hooks in order inside a guaranteed cleanup flow.
    
    Returns list of exceptions if any hook failed, but continues executing
    all subsequent post hooks (e.g. ensuring 'save-on' is called even if an earlier hook failed).
    """
    errors: List[Exception] = []
    for hook in hooks:
        if hook.strip():
            try:
                run_single_hook(hook, env=env, rcon=rcon)
            except Exception as e:
                logger.error("Post-hook failed: %s", e)
                errors.append(e)
    return errors
