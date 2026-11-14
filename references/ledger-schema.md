# Ledger schema and CLI

Depth for `SKILL.md`: the DDL, the open/lock/migration shape, the command surface and the pack the
agent reads after a purge. The reference implementation is `scripts/ledger.py`; keep them in sync.

## Storage

One database per **(scope, agent)**: `<dir>/<scope>.<agent>.db`, or `<dir>/<scope>.db` when the agent
is `default`. Default dir `.ledger`; default scope is the directory name; default agent is
`$HERMES_PROFILE` when set, else `default`. A sidecar `.lock` file sits beside it, and SQLite's
`-wal`/`-shm` files appear while it is open.

Two bots on one repository must not share a file: the orchestrator's requests would appear as the
coder's open work. `team` reads every `<scope>*.db` in the directory and prints each agent's open
tasks with an `agent/T0001` prefix.

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

$L init [--items 80]                     # create; prints the path, stores the item budget
$L request "Purgar contexto sin perder el hilo"     # -> T0001 (verbatim, actor=user)
$L action  --task T0001 "ledger.py written" --evidence "scripts/ledger.py"
$L result  --task T0001 "CLI works" --evidence "11 tests OK"   # evidence is required
$L failure "storing a paraphrase of the request" --evidence "the exact wording is lost"
$L decision "one SQLite file per scope" --why "projects must not bleed"
$L note    "the deploy key lives in the vault, not here"
$L close   --task T0002 --status blocked --note "waiting on the repo"
$L resume  [--items N] [--budget CHARS]  # the pack
$L team                                  # every agent's open work for this scope
$L search  "zip-slip OR extractall"      # FTS5, with evidence
$L status  [--json]                      # counts per status
$L check                                 # integrity; exit 1 on problems
$L reindex                               # rebuild the FTS table
```

`--scope` defaults to the working directory's name: the scope comes from where you are, never from the
prompt. `--agent` resolves in this order: `--agent`, `$HERMES_PROFILE`, `profiles/<name>` parsed out
of `$HERMES_HOME`, then `default` — a bot's shell does not export `HERMES_PROFILE`, so without the
`HERMES_HOME` step every bot silently writes into the `default` ledger. `--dir` defaults to `.ledger`.

## The pack (`resume`)

The exact sections, in order, and why each one is there:

Section sizes come from one number, `--items` (default 80), split by what is expensive to lose:

| Section | Share | At `--items 80` | Why |
|---|---|---|---|
| Open work | 50% (min 10) | up to 40 | every `open` / `in_progress` / `blocked` task with its last event |
| Recent work, with its evidence | 15% (min 4) | up to 12 | proves what is done; `NO EVIDENCE` is a flag |
| Decisions that constrain the next step | 12% (min 3) | up to 9 | the *why* is what a fresh session cannot reconstruct |
| Already tried and failed — do not repeat | 10% (min 3) | up to 8 | the most expensive line to lose |
| Notes worth keeping | 8% (min 2) | up to 6 | context that belongs to no task |
| Recently closed | 5% (min 3) | up to 4 | so "did I already finish this?" has an answer |

`init --items N` stores the preference in `meta`; `resume --items N` overrides it for one call. The
character budget defaults to `items × 160` (80 items → 12 800 chars) and is a second, hard bound with
a `… truncated` marker for a single oversized entry. A pack much past that stops being read, which is
the same as not having one — raise `--items` only when the next session genuinely needs the depth.

## Integrity (`check`)

Problems (exit 1) — each one means the ledger is lying about the work:

- a task `done` with no `result` event carrying evidence;
- a `result` event with empty evidence;
- an event whose text still matches a credential pattern (a write bypassed redaction).

Warnings — the ledger is drifting:

- tasks with no activity for 14 days (stale, or abandoned without being closed);
- events missing from the FTS index (`reindex`).

## Sizing notes

- The pack is read at every session start: 80 items is ~12 KB, which is the honest ceiling for a
  brief. Beyond that, let `search` carry the depth.
- `events` grows ~1 row per action; a year of daily work is a few MB, and FTS5 keeps queries flat.
- `failure` and `decision` are the two kinds that earn their space back. Never prune them first.
