# Enforcement: how the ledger stops being optional

A skill that says "use the ledger" is a suggestion. This is the mechanism that makes it a rule, and
the two things that were measured wrong on the way here.

## The hook, and the tool call it inspects

A `pre_tool_call` shell hook runs before every tool call with a JSON payload on stdin:

```json
{"hook_event_name": "pre_tool_call", "tool_name": "write_file",
 "tool_input": {"path": "/repo/app.py"}, "cwd": "/home/you", "session_id": "..."}
```

The hook answers with a directive on stdout, and silence means "allowed":

```json
{"decision": "block", "message": "…why, and the exact command that fixes it…"}
```

Two rules make the difference between a guard and an obstacle:

- **The block message is the fix.** It names the ledger command that unblocks the call and the one
  that closes the record. A bot that cannot self-serve from the message will retry the same call.
- **Reads never block.** Only `write_file`, `patch` and shell `git commit`/`git push` are inspected.
  Blocking `terminal` wholesale stops agents from reading, which is the opposite of the point.

`scripts/ledger_guard_hook.py` is that hook. It resolves the call site from the target path (not from
the process cwd, which is `HERMES_HOME` inside a bot), walks up to the repository, derives the scope
from the remote name, and takes the agent from `$HERMES_PROFILE` (falling back to the profile
directory in `$HERMES_HOME`). Then it asks the ledger `status --json` and decides.

## Wiring it into a profile

```yaml
# ~/.hermes/profiles/<profile>/config.yaml
hooks_auto_accept: true      # approve the hook without a TTY prompt; headless profiles need this
hooks:
  pre_tool_call:
    - command: /home/you/.hermes/scripts/ledger_guard_hook.py
      matcher: 'write_file|patch|edit_file|apply_patch|multi_edit|terminal'
      timeout: 30
```

`hooks_auto_accept: true` is what approves the script on a profile with no terminal. Without it the
hook is registered but never fires, and `hermes hooks doctor` is the only thing that says so:

```
✗ not allowlisted — hook will NOT fire at runtime
```

That line is about the allowlist file, not the config opt-in: with `hooks_auto_accept: true` the hook
does fire even while `hooks doctor` still reports it as un-allowlisted (measured — the block landed).
Treat the doctor's message as "no approval recorded", and confirm enforcement with a real turn.

Run `hermes hooks doctor` (with the profile's `HERMES_HOME`) after wiring, and again after editing
the hook: an approval is tied to the script's mtime, so a changed script is a new script.

## What did not work: plugin `pre_tool_call`

The obvious route is a Hermes plugin that registers a `pre_tool_call` callback. It was written, it
passed its own unit checks, `hermes plugins list` reported it enabled — and the probe it wrote on
every invocation stayed empty across three real runs:

- `hermes -z "<prompt>"` (quiet one-shot): hook never called, the file was written anyway;
- `hermes -p <profile> chat --continue "Bot Chat" -q "<prompt>"`: same, `write_file` completed;
- the plugin's own `register(ctx)` + `ctx.register_hook("pre_tool_call", fn)` shape was correct,
  copied from a plugin that had been in production on those profiles.

The shell-hook path (config-driven, dispatched from `agent/shell_hooks.py`) blocked the same call on
the first try. So: enforce with shell hooks, and keep the plugin out of the story entirely rather
than shipping something that reads as enabled and silently does nothing.

A plugin is still the right tool when you need to *rewrite* arguments or see the tool result. For a
policy gate, the config-level hook is both simpler and the one that demonstrably fires.

## Rollout, in the order that works

1. Install the CLI and the three skills (`scripts/install.sh`).
2. Create the ledger for the scope: `ledger init --items 100`, then record the standing work
   (`request`, `decision`) so the first `resume` has something in it.
3. Put the rule in the files agents actually read *before* enabling the hook: `AGENTS.md` in the
   repository, `HANDOVER.md`/`SOUL.md` in the profile. The hook is the stick; this is the map.
4. Wire the hook last — after step 2, so the first blocked call has a ledger to point at and an
   `init` is not the thing the bot learns first.
5. Verify with a real turn, not a unit test: ask the bot to write one file and confirm it comes back
   with the block message instead of a filesystem change.

## Failure modes seen in practice

- **Scope from the process cwd.** Bots run with `cwd=$HERMES_HOME`, so the first version resolved
  scope `.hermes` and told a coder to init a ledger for the home directory. Resolve from the target
  path in `tool_input`, then from `cwd`.
- **Agent from `$HERMES_PROFILE` alone.** Gateways do not always export it; the hook falls back to
  parsing `profiles/<name>` out of `$HERMES_HOME`.
- **Blocking every `terminal` call.** Reads stop, the agent cannot even look at the ledger it is
  being told to use. Match only `git commit`/`git push` inside the command string.
- **A missing ledger that allows the call.** It reads as "bootstrap friendly" and becomes the normal
  state: nothing is ever recorded. Block, and put `init` in the message.
- **The escape hatch in the bot's hands.** `LEDGER_GUARD=off` must exist for a human debugging a
  stuck session; say in the instructions that bots do not use it, and check the ledger afterwards to
  see whether anything was recorded while the guard was off.
