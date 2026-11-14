# Ledger schema and CLI

Depth for `SKILL.md`: the DDL, the open/lock/migration shape, the command surface and the pack the
agent reads after a purge. The reference implementation is `scripts/ledger.py`; keep them in sync.

## Storage

One database per scope: `<dir>/<scope>.db` (default dir `.ledger`, default scope = directory name).
A sidecar `.lock` file sits beside it, and SQLite's `-wal`/`-shm` files appear while it is open.

```sql
CREATE TABLE meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);

CREATE TABLE tasks(
    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, closed_at TEXT, note TEXT NOT NULL DEFAULT '');

CREATE TABLE events(
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, task_id TEXT,
    text TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'agent');

CREATE VIRTUAL TABLE events_fts USING fts5(text, evidence, why);
CREATE INDEX events_task ON events(task_id);
```

`kind` is one of `request`, `action`, `result`, `failure`, `decision`, `note`. `status` is one of
`open`, `in_progress`, `done`, `blocked`, `dropped`. Times are UTC ISO-8601.

Two things are deliberately not tables:

- **The event list is the source of truth.** A task's status is a projection maintained by the events
  that touch it. That is what lets `check` say "this task is done with no result event" — a state
  column written by hand cannot be audited.
- **The summary tree lives outside the hot path.** Compaction writes rows into the same file (a
  `summaries` table when you add it), but the agent's resume never depends on it: the pack reads
  events. If the summarizer breaks, the ledger still works.

## Opening, locking, migrating

```python
conn = sqlite3.connect(f'file:{path}?mode=rw', uri=True)   # missing file => error, never created
conn.execute('PRAGMA journal_mode=WAL')
conn.execute('PRAGMA foreign_keys=ON')
version = conn.execute('PRAGMA user_version').fetchone()[0]
for target, migration in MIGRATIONS:                      # [(1, sql), (2, sql), ...]
    if version < target:
        conn.executescript(migration); conn.execute(f'PRAGMA user_version={target}')
```

- `mode=rw`, not `mode=rwc`: creation is a privilege of `init`. A late job that opens a deleted scope
  then fails instead of resurrecting it.
- The lock is `fcntl.flock(LOCK_EX)` on `<scope>.lock`, held across append → read → answer. On Windows
  `fcntl` is absent; use `msvcrt.locking` or run single-writer.
- Migrations are additive and idempotent: `user_version` is bumped inside the same transaction as the
  DDL, so a crash mid-migration repeats instead of half-applying.

## Command surface (`scripts/ledger.py`)

```bash
L="python3 scripts/ledger.py --dir .ledger --scope my-project"

$L init                                  # create; prints the path
$L request "Purgar contexto sin perder el hilo"     # -> T0001 (verbatim, actor=user)
$L action  --task T0001 "ledger.py written" --evidence "scripts/ledger.py"
$L result  --task T0001 "CLI works" --evidence "11 tests OK"   # evidence is required
$L failure "storing a paraphrase of the request" --evidence "the exact wording is lost"
$L decision "one SQLite file per scope" --why "projects must not bleed"
$L note    "the deploy key lives in the vault, not here"
$L close   --task T0002 --status blocked --note "waiting on the repo"
$L resume  [--budget 4000]               # the pack
$L search  "zip-slip OR extractall"      # FTS5, with evidence
$L status  [--json]                      # counts per status
$L check                                 # integrity; exit 1 on problems
$L reindex                               # rebuild the FTS table
```

`--scope` defaults to the working directory's name: the scope comes from where you are, never from the
prompt. `--dir` defaults to `.ledger`.

## The pack (`resume`)

The exact sections, in order, and why each one is there:

| Section | Rows | Why |
|---|---|---|
| header | 1 | scope, open count, budget |
| Open work | up to 40 | every `open` / `in_progress` / `blocked` task with its last event |
| Decisions that constrain the next step | 6 | the *why* is what a fresh session cannot reconstruct |
| Already tried and failed — do not repeat | 5 | the most expensive line to lose |
| Recent work, with its evidence | 8 | proves what is done; a line reading `NO EVIDENCE` is a flag |
| Notes worth keeping | 6 | context that belongs to no task |
| Recently closed | 5 | so "did I already finish this?" has an answer |

Bounded twice: a per-section row limit (the normal case) and a hard `--budget` in characters with a
`… truncated` marker (for one oversized entry). A pack over ~4 KB stops being read by the agent, which
is the same as not having one.

## Integrity (`check`)

Problems (exit 1) — each one means the ledger is lying about the work:

- a task `done` with no `result` event carrying evidence;
- a `result` event with empty evidence;
- an event whose text still matches a credential pattern (a write bypassed redaction).

Warnings — the ledger is drifting:

- tasks with no activity for 14 days (stale, or abandoned without being closed);
- events missing from the FTS index (`reindex`).

## Sizing notes

- The pack is read at every session start: keep it under ~4 KB, and let `search` carry depth.
- `events` grows ~1 row per action; a year of daily work is a few MB, and FTS5 keeps queries flat.
- `failure` and `decision` are the two kinds that earn their space back. Never prune them first.
