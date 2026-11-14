# Anti-patterns, measured

Each of these was observed in real agent workflows — ours or another team's — and each one produced a
specific, expensive failure. They are ordered by how much they cost, not by how easy they are to avoid.

## 1. A ledger of requests only

The agent knows every request and nothing it did about them. Symptoms: it re-implements a function
that already exists, re-opens a design that was rejected with reasons, and reports the same task as
"starting now" for the third session.

**Fix:** every request is a task; no task is closed without a `result` event carrying evidence.

## 2. A ledger of actions only

The agent dutifully records what it did and cannot tell a finished task from an abandoned one, because
nothing ties an action to a request. It reports progress on work nobody asked for and stays silent about
what was asked and dropped.

**Fix:** actions take `--task`; a task that nobody closes shows up in the open list, which is the
signal.

## 3. Storing a paraphrase of the request

The classic failure. "Quiero que el agente no pierda el hilo" is stored as "implement memory". Two
sessions later the ledger "remembers" a different project than the one that was asked for, and there is
no way to tell: the original words are gone.

**Fix:** `request` takes the text verbatim. If it needs restructuring, structure it in a *second* note,
never in place of the words.

## 4. `done` without evidence

The claim nobody can check. It passes review, it makes the pack look clean, and it hides the case where
the fix was applied to the wrong branch, or the test was never run.

**Fix:** `result` requires `--evidence`, and `check` fails a `done` task without one. Evidence is the
command and its output, the test name, or `path:line` — never "done".

## 5. A summary that invents completion

The most expensive one. An abandoned task folds into a summary reading "completed", the open list
shrinks, and the work is silently gone while every dashboard is green.

**Fix:** state derives from events; a summary claiming completion without a supporting `result` event
is rejected and the node is marked `truncated`. See `prompt-compact.md`.

## 6. Dropping the failures

Failures feel like noise in a work log, so they get pruned first. Then the next session explores the
same dead end — the same library that does not install, the same approach rejected for a reason nobody
wrote down.

**Fix:** `failure` is a first-class kind, rendered in the pack, and never pruned before `note`.

## 7. No compaction, or a pack that outgrew its budget

The ledger grows until `resume` produces 40 KB, the agent skims it, then stops calling it. The system
is "in place" and unused — the most common way this idea dies.

**Fix:** the pack is bounded per section and by a hard budget; depth lives behind `search`.

## 8. Retrieval only by keyword

"The agent remembers what it can name." The person says *"lo del otro día con los permisos"* and the
search finds nothing because the event says "authorization matrix". Memory then looks broken exactly
when it is needed.

**Fix:** FTS5 over every event, plus the last N events unconditionally in the pack; retrieval is a
ranking problem, not a filter.

## 9. One global ledger for every project

Every request appears in every project's pack. A decision taken for one repo shows up as a constraint
in another, and the agent applies it. The pack also becomes unusable long before it is unbiased.

**Fix:** one database per scope, resolved from the working directory.

## 10. Credentials in the log

"It is only local." It is local in a file that is copied into backups, pasted into an issue, and
committed once by mistake. The ledger is long-lived by design, which is the worst property a secret can
have.

**Fix:** redaction at write time, and `check` fails when an event still matches a credential pattern.

## 11. Concurrent writers

A cron worker compacts while the agent appends; two events get the same task's `in_progress` and one
overwrites the other's status. Nothing crashes, and the state is wrong.

**Fix:** `flock` per scope across append → read → answer, in every process that touches the file.

## 12. Trusting the ledger to be the whole context

The ledger says what was asked and done; it cannot say what the code looks like now. A session that
resumes and starts editing from the pack alone, without re-reading the changed files, applies a fix to
a function that no longer exists.

**Fix:** `resume` is the *first* step, not the only one: the pack tells you where to look, the files
tell you what is true. Re-read before editing.
