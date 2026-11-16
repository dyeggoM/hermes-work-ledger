#!/usr/bin/env python3
"""Work-ledger guard, wired as a Hermes shell hook on ``pre_tool_call``.

Contract (agent/shell_hooks.py): a JSON payload on stdin, an optional JSON directive on stdout.
This hook blocks a mutating call when the calling bot has no open task in the ledger of the current
scope, and stays silent otherwise.

Why a shell hook and not a plugin: plugin ``pre_tool_call`` callbacks did not fire on this host's CLI
and chat paths (measured, see references/enforcement.md), while shell hooks are dispatched from the
config with per-tool matchers and can be exercised with ``hermes hooks test``.

Policy, deliberately narrow:

- reads never block: only ``write_file`` / ``patch`` and shell ``git commit|push`` are inspected;
- a scope with no ledger blocks too, but the message carries ``init`` + ``request`` so one step fixes it;
- ``LEDGER_GUARD=off`` in the environment disables the hook (a human escape hatch);
- any internal failure allows the call: the guard is a policy, not a dependency.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys

# Convention: the installer puts the wrapper in <hermes-home>/scripts/ledger. Override with LEDGER_CLI
# when the wrapper lives somewhere else (another home, a container, a test harness).
HELPER = os.environ.get("LEDGER_CLI") or os.path.expanduser("~/.hermes/scripts/ledger")
MUTATING = {"write_file", "patch", "edit_file", "apply_patch", "multi_edit"}
COMMIT_RE = re.compile(r"\bgit\s+(?:-C\s+\S+\s+)?(?:commit|push)\b")


def needs_task(tool: str, args: dict) -> bool:
    if tool in MUTATING:
        return True
    return tool == "terminal" and bool(COMMIT_RE.search(str(args.get("command") or "")))


def profile_agent() -> str:
    """Which bot is calling. The hook subprocess inherits HERMES_HOME, not always HERMES_PROFILE."""
    explicit = os.environ.get("HERMES_PROFILE")
    if explicit:
        return explicit
    m = re.search(r"/profiles/([^/]+)/?$", os.environ.get("HERMES_HOME") or "")
    return m.group(1) if m else "default"


def call_site(args: dict, payload: dict) -> str:
    """The directory the call is about: the target file's folder, else the session's cwd."""
    for key in ("path", "file_path", "filePath", "path_or_url", "target_file"):
        value = args.get(key)
        if isinstance(value, str) and value.strip():
            path = os.path.abspath(os.path.expanduser(value))
            return path if os.path.isdir(path) else os.path.dirname(path)
    return str(payload.get("cwd") or os.getcwd())


def repo_root(start: str) -> str | None:
    """Nearest directory at or above ``start`` that is a git checkout, else None."""
    path = os.path.abspath(start or ".")
    while True:
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent


def status(cwd: str, agent: str):
    try:
        out = subprocess.run([HELPER, "--agent", agent, "status", "--json"], cwd=cwd or None,
                             capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0 or not out.stdout.strip():
        return None
    try:
        return json.loads(out.stdout)
    except json.JSONDecodeError:
        return None


def main() -> int:
    if os.environ.get("LEDGER_GUARD", "on").strip().lower() in {"off", "0", "false", "no"}:
        return 0
    try:
        payload = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        return 0
    tool = str(payload.get("tool_name") or "")
    args = payload.get("tool_input") or payload.get("args") or {}
    if not isinstance(args, dict) or not needs_task(tool, args):
        return 0

    agent = profile_agent()
    site = call_site(args, payload)
    root = repo_root(site) or repo_root(str(payload.get("cwd") or ""))
    if root is None:
        # A file outside every checkout (scratch, cache, /tmp) changes no project's code, so there is
        # no scope whose ledger it belongs to. Blocking it only stops the bot from working — and the
        # guard is about what gets recorded, not about forbidding temporaries. Measured: resolving the
        # scope from the path alone created a phantom scope 'scratch' and blocked a backfill script.
        return 0
    state = status(root, agent)
    if state is None:
        return 0
    scope, agent = state.get("scope", "this scope"), state.get("agent", "this profile")

    if not state.get("exists", True):
        msg = (f"Blocked by the work ledger: {agent} has no ledger for scope {scope!r}. "
               "Create it and record what you were asked before changing files:\n"
               f"  {HELPER} init\n"
               f'  {HELPER} request "<the assignment, as it was given to you>"\n'
               "then retry this call.")
    else:
        tasks = state.get("tasks") or {}
        if tasks.get("open", 0) or tasks.get("in_progress", 0):
            return 0
        msg = (f"Blocked by the work ledger: {agent} has no open task in scope {scope!r}, and this call "
               "would change files. Record what you were asked first, verbatim:\n"
               f'  {HELPER} request "<the assignment, as it was given to you>"\n'
               "then retry this call. What is already open:\n"
               f"  {HELPER} resume\n"
               "When you deliver, close the record with evidence:\n"
               f'  {HELPER} result --task T0001 "<what you delivered>" --evidence "<command/test/path>"\n'
               "Escape hatch for a human: LEDGER_GUARD=off (bots: do not use it).")

    print(json.dumps({"decision": "block", "message": msg}))
    return 0


if __name__ == "__main__":
    sys.exit(main())
