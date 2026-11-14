# work-ledger

A Hermes skill — and a small CLI — for keeping an **append-only record of what was asked and what was
done**, so the context window can be thrown away often without the agent losing the thread.

`/new`, a compaction, a crash, a handover: the conversation is gone and the work is not. One SQLite
file per scope, full-text search over the event log, and a bounded *pack* that a fresh session reads
before it touches anything.

It is not the agent's own memory of the conversation. It is the memory of the **work**: the requests in
the requester's own words, the decisions and their reasons, the evidence for what was finished, and
the dead ends so they are not explored twice.

## Layout

```
SKILL.md                        the operating skill: the rule, one turn, resume, compaction, tests
references/ledger-schema.md     DDL, open/lock/migration, the CLI surface, the pack format
references/prompt-compact.md    the summarizer contract, including the "never invent completion" guard
references/anti-patterns.md     twelve measured failure modes and the fix for each
references/rollout-phases.md    adoption order, the purge test and the isolation test
scripts/ledger.py               reference implementation (stdlib only, Python 3.10+)
tests/test_ledger.py            11 tests: verbatim requests, evidence-required results, redaction,
                                bounded pack, FTS search, scope isolation, integrity check
```

## Why per scope, and why both sides

- **Per scope.** One ledger per repository or deployment. A shared ledger makes a request for one
  project show up as pending in another, and a local decision look like a global rule.
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
```

`result` refuses to run without `--evidence`, and `check` fails a task marked done without one. That
asymmetry is deliberate: "done" is a claim, the artifact is proof.

## What the pack looks like

```
# Ledger · my-project

2 open · 3 recently closed · pack 4000 chars

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
