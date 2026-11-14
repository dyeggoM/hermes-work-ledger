# The compaction contract

Old events fold into one summary per task. The summarizer runs on a cheap model, on a background
queue, never in the requester's latency — and its output is **prose about events**, which is exactly
why it needs a contract. This is that contract.

## What a task summary must keep, in this order

1. **The request, verbatim.** The words the person used, not a paraphrase. Everything downstream is
   judged against this line, and a paraphrase is indistinguishable from a misreading.
2. **The decisions and their why.** A decision without its reason is a rule the next session will
   either break or obey blindly.
3. **Evidence pointers.** `path:line`, the command, the test name — enough to re-check the claim
   without re-doing the work.
4. **What failed and why.** The dead ends are the cheapest thing to store and the most expensive to
   rediscover.
5. **Where it stopped and what comes next.** One line. This is the field the next session reads first.
6. **Open questions for the person**, if any, quoted.

Not wanted: narrative, praise, a restatement of the code, or any statement about work that no event
supports.

## Hard rules

- **State is derived, never invented.** If the summary says `done`, a `result` event with evidence must
  exist. A summary that claims completion on an abandoned task is the worst failure mode of the whole
  system: the work is gone *and* the report is green. The writer rejects the summary and marks the node
  `truncated`, which forces a rebuild.
- **Every artifact named must exist in the source events.** Extract paths, commands and test names from
  the summary and assert each appears in the events it covers. Cheap, and it catches the invented
  `tests/test_does_not_exist.py`.
- **Size cap** (e.g. 1 200 bytes) with the size fed back in the retry prompt; after three failures the
  node is written as `truncated` with a pointer to the event range, never as a low-quality summary.
- **One batch = one task.** A summary spanning several tasks is how a blocked task inherits another's
  completion.
- **No cross-scope input, ever.** The prompt is built from one database; a "similar scope" example is
  how a sentinel leaks from A to B.
- **Circuit breaker** on 429/timeouts/5xx with exponential backoff and an age ceiling: at the ceiling
  the node is `truncated`, and a sweep at boot retries it. No 10-second retry loop.

## Suggested prompt shape

```
You are compacting one task's history for an engineering ledger.
Input: the task title, then the events (kind, at, text, evidence, why), oldest first.

Write at most 1200 bytes with exactly these headings:
- Request (verbatim, no editing)
- Decisions (each with its why)
- Evidence (path:line / command / test, one per line)
- Failed attempts (what was tried, why it failed)
- State (open | blocked | done — and only if a result event with evidence exists)
- Next action (one line)

Rules: do not add facts that are not in the events. Do not name a file, command or test that does not
appear in the events. If the history is contradictory, say so in one line instead of resolving it.
```

The output is validated by code before it is stored; the model is a writer, not the authority.

## Where the summary is used

- Never in place of an open task in the pack: open tasks are always rendered from events.
- As the tail of a closed task, so the historical part of the pack stays small.
- As the input to `search` ranking boosts, not as the retrieval corpus itself.

## What compaction must not touch

Open tasks, their last event, and anything from the current session. The pack must show live work from
the event log even when the summarizer is hours behind or broken.
