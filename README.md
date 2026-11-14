# work-ledger

A Hermes skill — and a small CLI — for keeping an **append-only record of what was asked and what was
done**, so the context window can be thrown away often without the agent losing the thread.

`/new`, a compaction, a crash, a handover: the conversation is gone and the work is not. One SQLite
file per scope, full-text search over the event log, and a bounded *pack* that a fresh session reads
before it touches anything.

It is not the agent's own memory of the conversation. It is the memory of the **work**: the requests in
the requester's own words, the decisions and their reasons, the evidence for what was finished, and
the dead ends so they are not explored twice.

## Install

```bash
./scripts/install.sh          # symlink the skill into the skills dir, install the CLI and the hook
./scripts/install.sh --copy   # copy the skill instead of symlinking it
HERMES_HOME_DIR=/srv/hermes ./scripts/install.sh
```

Everything lands under the Hermes home (`~/.hermes` by default), so no path in this repository is
specific to one machine:

```
<hermes-home>/scripts/ledger                 the wrapper every agent calls
<hermes-home>/scripts/ledger_impl.py         the CLI
<hermes-home>/scripts/ledger_guard_hook.py   the pre_tool_call guard
<hermes-home>/ledgers/                       one SQLite per scope and agent
<hermes-home>/skills/personal/work-ledger    this skill
```

The wrapper takes the scope from the repository name and the agent from the profile, so an agent
never passes either. `LEDGER_DIR`, `LEDGER_SCOPE`, `LEDGER_PY`, `LEDGER_CLI` and `HERMES_PROFILE`
override the defaults when a host lays things out differently.

## Layout

```
SKILL.md                        the operating skill: the rule, one turn, resume, compaction, tests
references/ledger-schema.md     DDL, open/lock/migration, the CLI surface, the pack format
references/prompt-compact.md    the summarizer contract, including the "never invent completion" guard
references/anti-patterns.md     twelve measured failure modes and the fix for each
references/rollout-phases.md    adoption order, the purge test and the isolation test
references/enforcement.md       the guard: config, rollout order, and what does not fire
scripts/ledger.py               reference implementation (stdlib only, Python 3.10+)
scripts/ledger                  wrapper: central dir, scope from the repository
scripts/install.sh              CLI + hook + skill into a Hermes home
scripts/install-enforcement.sh  wire the hook into one or more profiles
tests/test_ledger.py            18 tests: verbatim requests, evidence-required results, redaction,
                                bounded pack, FTS search, scope and agent isolation, integrity check
```

## Enforcement

`scripts/ledger_guard_hook.py` is a Hermes `pre_tool_call` shell hook: it blocks `write_file`,
`patch` and `git commit`/`git push` while the calling bot has no open task in the scope, and returns
the exact command that unblocks it. Reads are never blocked. `scripts/install-enforcement.sh <profile>`
wires it into a profile's `config.yaml`; the rule it enforces is stated in the repository's
`AGENTS.md` and the profile's `HANDOVER.md`/`SOUL.md`.

Why shell hooks and not a plugin — the plugin path was tried, was reported as enabled, and never
fired on this host: `references/enforcement.md` has the measurements and the rollout order.

## Why per scope *and* per agent, and why both sides

- **Per (scope, agent).** One file per repository *and* per bot: `my-service.lead.db` next to
  `my-service.worker.db`. Pooling them means each bot sees the other's tasks as its own open work,
  and "what did I do" has no answer. `ledger team --scope my-service` prints every agent's open work for the
  scope without merging the memories.
- **Both sides.** Requests only and the agent redoes work and re-proposes rejected designs. Actions
  only and it cannot tell finished from abandoned. The pair is what makes the record usable.

## Use it

```bash
L="python3 scripts/ledger.py --dir .ledger --scope my-project"

$L init
$L request "keep the ledger across context purges"      # -> T0001, stored verbatim
$L action  --task T0001 "wrote the CLI" --evidence "scripts/ledger.py"
$L result  --task T0001 "CLI works" --evidence "11 tests OK"
$L failure "stored a paraphrase of the request" --evidence "the exact wording was lost"
$L decision "one SQLite file per scope" --why "projects must not bleed"
$L resume                                               # the pack a fresh session reads first
$L check                                                # integrity: evidence, secrets, stale work

# one bot per ledger, same repository
python3 scripts/ledger.py --scope my-service --agent lead request "prioritise the round"
python3 scripts/ledger.py --scope my-service --agent lead resume
python3 scripts/ledger.py --scope my-service team          # every agent's open work, side by side

# capacity: one number, default 80 items, stored per ledger
python3 scripts/ledger.py --scope my-service --agent lead init --items 200
python3 scripts/ledger.py --scope my-service --agent lead resume --items 40   # one-off
```

`result` refuses to run without `--evidence`, and `check` fails a task marked done without one. That
asymmetry is deliberate: "done" is a claim, the artifact is proof.

## What the pack looks like

```
# Ledger · my-project · lead

2 open · 3 recently closed · up to 80 items · pack 12800 chars

## Open work
- T0003 [open] purgar contexto sin perder el hilo
  last 2026-11-14T09:02: the skill needs a resume step before /new
## Decisions that constrain the next step
- 2026-11-14 one SQLite file per scope — why: projects must not bleed
## Already tried and failed — do not repeat
- 2026-11-14 storing a paraphrase of the request (the exact wording was lost)
## Recent work, with its evidence
- 2026-11-14 CLI works · 11 tests OK
```

## Status

`0.1.0` — skill, reference implementation and tests. Open work if this repo grows: a compaction worker
with the `prompt-compact.md` guards enforced by code, a session-start hook for Hermes that runs
`resume` automatically, and a recall benchmark (plant N requests/decisions/failures, purge, measure how
much of the pack is recoverable).

MIT — see [`LICENSE`](LICENSE).
