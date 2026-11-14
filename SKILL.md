---
name: work-ledger
description: "Use when purging context. Recall what was asked and what was done."
version: 0.1.0
author: Hermes Agent
license: MIT
platforms: [linux, macos]
metadata:
  hermes:
    tags: [memory, tasks, ledger, sqlite, fts5, handover, compaction, context]
    related_skills: [hermes-agent, session-librarian]
---

# Work ledger

## When to Use

Load this when work has to survive the context window: an agent that runs long tasks across sessions,
a handover, a `/new` or a compaction every few turns, a queue of client requests, or any workflow
where "recuerda lo que te pedí" must still be true next week.

It answers two questions at any moment, without the chat log:

- **what was asked** — verbatim, with what is still open;
- **what was done about it** — with the evidence that proves it, and what was already tried and failed
  so it is not tried twice.

It is not the agent's own working memory (compaction, a summary of the conversation, a handover note):
those describe the *conversation*. This describes the *work*, outlives every conversation, and is what
makes aggressive purging safe.

## The rule that decides everything else

**A ledger belongs to a scope, and it records both sides of the work.**

- *Scope* is the unit that owns its own commitments: a repository, a deployment, a client. One ledger
  per scope, never one global file — two projects in one ledger means a request for A shows up as
  pending while working on B, and a decision taken for A silently constrains B.
- *Agent* is who is doing the work. A ledger is per **(scope, agent)** pair: `imc.orchestrator.db`,
  `imc.opencode-coder.db`. Two bots on one repository that share a ledger see each other's tasks as
  their own open work and answering "what did *I* do" becomes impossible; the same applies to a worker
  and the bot that reviews it. `ledger team` is the read-only view across agents for one scope, which
  is how the orchestrator sees what each bot is carrying without pooling their memory.
- *Both sides* matters because they fail differently. Requests without actions and the agent knows
  what to do but not what it did: it redoes finished work and re-proposes rejected ideas. Actions
  without requests and it knows what it did but not why: it cannot tell a finished task from an
  abandoned one, and it reports progress on something nobody asked for.

The unit is the **task**, and its state is *derived from events*, never typed in by hand.

## Making it a rule, not a suggestion

A `pre_tool_call` shell hook (`scripts/ledger_guard_hook.py`) blocks `write_file`, `patch` and shell
`git commit|push` while the bot has no open task in the scope, and the block message carries the
command that fixes it. Wire it per profile with `scripts/install-enforcement.sh <profile>...`; the
config block, the `hooks_auto_accept` requirement and the failure modes are in
`references/enforcement.md`.

Enforce in this order: create the ledger and record the standing work first, put the rule in the
files the bot reads (`AGENTS.md`, `HANDOVER.md`/`SOUL.md`) second, wire the hook last. A hook that
fires before the ledger exists teaches the bot to argue with it.

## The ledger: one file per scope and agent

`<dir>/<scope>.<agent>.db` (`<scope>.db` when the agent is `default`) — SQLite, one file, driven by
`scripts/ledger.py`. DDL, CLI surface and the pack format are in `references/ledger-schema.md`.

- `events` — append-only: `request`, `action`, `result`, `failure`, `decision`, `note`. Corrections are
  new events; history is never rewritten, because the value of the ledger is that it can be audited
  against what actually happened.
- `tasks` — `open`, `in_progress`, `done`, `blocked`, `dropped`; created by a `request`, moved by the
  events that reference them. A `done` without a `result` event carrying evidence is an integrity
  error, not a state.
- `fts` — FTS5 over the events, so an old decision is one query away instead of one search through
  months of chat.
- `PRAGMA user_version` — migrations run on open.

Rules that make it trustworthy:

- **A missing ledger is an error, never silently created.** Only `init` creates. A background job that
  opens the file must not resurrect a scope deleted on purpose.
- **One writer per scope**: an `fcntl` lock on `<scope>.lock`, taken by the agent and by any worker,
  for the whole read-modify-write. Two writers appending concurrently interleave task states.
- **Redaction at write time**, not at read time: keys, tokens, passwords and private-key blocks become
  `[redacted]` before they enter the file. A ledger is long-lived by design, which makes it exactly the
  wrong place for a credential.

## One turn, in order

1. **Resolve the scope from the working directory, never from the prompt.** The prompt can be wrong
   about which project it is; the path cannot.
2. **Take the lock and append the inbound request verbatim** — the words, not a summary of them.
   Paraphrasing is where "lo que pedí" becomes "lo que el agente creyó que pedí".
3. **Retrieve**: FTS5 over requests, decisions and failures for the current intent, plus every open
   task. People test memory without warning; an "only when it looks like a question" heuristic loses
   exactly the turn that mattered.
4. **Assemble the pack** (`resume`): open work, the decisions that constrain it, failed attempts,
   recent results with their evidence. Bounded — a pack that does not fit the budget stops being read.
   The size is one number: `--items` (default 80) splits across the sections by what is expensive to
   lose (open work half, evidence 15%, decisions 12%, failures 10%, notes 8%, closed 5%), and the
   character budget defaults to `items × 160`. Raise it for a long engagement (`init --items 200`),
   lower it for a chatty one; the choice is stored in the ledger.
5. **Do the work.**
6. **Append actions and the result with evidence**: the command and its real output, the test name,
   `path:line`. "Done" is not evidence; the artifact is.
7. **A failure gets its own event.** The failed attempt is the most valuable line in the file next
   week, and the one an agent is most tempted to omit.
8. **New requests discovered during the work become `request` events**, not notes: they are work, and
   they stay in the open list until someone closes them.

## The resume protocol

This is the whole point. After a purge, a `/new`, a crash or a handover, the **first action is
`ledger resume`** — before reading code, before re-planning.

The pack is the context. Then answer five questions out loud; if any is unanswerable the ledger is
deficient, and the fix is in the ledger, not in the story:

1. What is still open, in the requester's own words?
2. What did I already do about it, and where is the evidence?
3. What did I try that failed, and why am I not doing it again?
4. Which decision constrains the next step?
5. What is the single next action?

A fresh session that cannot answer (1) or (2) is paying for a context window it does not need.

## Compaction: background, bounded, refusing to invent completion

Old events fold into per-task summaries — off the critical path, never in the requester's latency.

The summarizer keeps, in order: the verbatim request · the decisions and *why* · evidence pointers ·
what failed and why · where the work stopped and what comes next.

Guards, because a summarizer fails in creative ways (`references/prompt-compact.md`):

- **State derives from events, not from prose.** A summary saying "completed" while no `result` event
  exists is rejected, not stored: that is how an abandoned task becomes a finished one.
- **Hard cap per summary** with size feedback on retry, then a `truncated` marker and a rebuild path.
- **Hallucination check**: every path, command and test name in a summary must appear in the events it
  summarises. Cheap regex; catches the invented artifact.
- **A circuit breaker** for 429/timeouts/5xx with an age ceiling; at the ceiling the node is
  `truncated`, not retried forever.
- The summarizer never sees another scope, and never runs on the most expensive model.

## Purging, deletion, redaction

Purging is the feature, not the risk: the ledger exists so the conversation can be thrown away.

- Around a purge, assert `status --json` before and after: the open-task count must be identical. A
  purge that loses an open request is a process bug, and this is the check that catches it.
- What must never be lost: open requests, decisions, failed attempts, evidence pointers.
- What may be lost: everything the events already say.

Deleting a scope, under the lock: tombstone → cancel queued jobs → close connections → rename to
`.deleting` → delete `.db`, `-wal`, `-shm` → fsync the directory → verify. A reconciler at boot
finishes `.deleting` leftovers. Deleting a person's data means their scopes plus redacting anything
that echoed their content into another scope's notes; "deleted" that leaves the request inside a
summary is cosmetic deletion.

## Prove it, don't claim it

Two tests before trusting the ledger with real work:

- **Purge test**: record three requests, complete one, purge the context, `resume`, and check the agent
  states the two open ones and the evidence of the closed one without re-reading the repo.
- **Isolation test**: plant a sentinel string in scope A; grep scope B's database and pack for it. It
  must be absent. This catches the class of leak a shared ledger guarantees.

`references/rollout-phases.md` carries both harnesses and the adoption order.

## Anti-patterns measured in real use

Full detail in `references/anti-patterns.md`:

- a ledger of requests only (the agent redoes work and re-proposes rejected designs);
- a ledger of actions only (finished and abandoned look identical);
- storing a paraphrase of the request instead of the request;
- `done` without a result event or evidence — the claim nobody can check;
- a summary that invents completion (the most expensive failure: the work is gone and the report is
  green);
- dropping failures, so the same dead end is explored every session;
- no compaction, so the pack outgrows the budget and the agent stops reading it;
- retrieval only by keyword ("the agent remembers only what it can name");
- one global ledger for every project;
- credentials in the log, because "it is only local".

## Checklist

- [ ] One ledger per **(scope, agent)**, keyed to the working directory and the bot's identity, never
      to the prompt; `team` for the cross-agent view.
- [ ] The pack size is a stated number (`--items`, default 80) and fits what the next session can read.
- [ ] Requests stored verbatim; failures and decisions stored with their reason.
- [ ] Tasks derived from events; `done` implies a `result` with evidence.
- [ ] `resume` is the first action of every fresh session, and fits the budget.
- [ ] Compaction off the critical path, with cap, hallucination check and no invented completion.
- [ ] Purge test and isolation test green.
- [ ] Redaction at write time; scope deletion leaves no trace.
