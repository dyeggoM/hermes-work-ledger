# Adoption, and the two tests that prove it

The ledger is cheap to start and easy to abandon. The order below is the one that survives contact
with real work: each phase is useful on its own, and each has a reversal.

## Phase 1 — one scope, manual

- `scripts/ledger.py init` in the project that hurts most (the one whose sessions keep re-discovering
  what was already done).
- Add `.ledger/` to that project's `.gitignore`: the ledger is local state, like `.security/` or
  `.code-inventory/`.
- Discipline, for one week: `request` when asked, `result --evidence` when finished, `failure` when a
  dead end is found, `resume` at the start of every session.
- **Reversal:** stop writing; nothing in the product depends on it.

Measure at the end of the week: re-done work avoided, sessions that started from the pack, and pack
size. If the pack is already over ~4 KB with one project, the compaction phase is not optional.

## Phase 2 — make `resume` automatic

- The session-start hook prints the pack (`ledger resume`) before the first turn.
- A `/new` or a handover starts with the pack, and the five questions in `SKILL.md` are answered in the
  first reply.
- **Reversal:** remove the hook; the ledger keeps recording, nothing breaks.

## Phase 3 — compaction

- A background job folds tasks closed more than N days ago into summaries (`prompt-compact.md`), with
  the guards: derived state, hallucination check, cap, circuit breaker.
- It runs *after* the pack is served, never before it.
- **Reversal:** disable the worker; the pack grows but still works, because it reads events.

## Phase 4 — more scopes

- One ledger per repo, and `--scope` resolved from the working directory in every entry point.
- A cross-scope view, if you need one, is a *report* that reads several packs — never a single merged
  database.

## The purge test

The point of the whole system. Run it before trusting a purge in production:

```
1. ledger request "A"          # will stay open
2. ledger request "B"          # will be completed
3. ledger result --task B ... --evidence ...
4. ledger request "C"          # will stay open
5. purge the conversation / start a brand-new session
6. first action: ledger resume
```

Pass criteria — answered from the pack alone, without reading the repository:

- the session names A and C, verbatim, as the open work;
- it states what B was and cites its evidence;
- it does not claim A or C are in progress;
- the open-task count before and after the purge is identical (`status --json`).

A failure here is a bug in the ledger, not in the model: a lost request was never recorded, or was
recorded as a paraphrase, or was folded away by a summarizer that invented its state.

## The isolation test

```
1. ledger --scope alpha init
2. ledger --scope alpha note "SENTINEL-ALPHA-<random>"
3. ledger --scope beta init
4. ledger --scope beta note "SENTINEL-BETA-<random>"
5. grep the beta database and `ledger --scope beta resume` for SENTINEL-ALPHA
```

Pass criteria: zero hits. Then repeat in the other direction. A hit means a shared file, a shared
summarizer prompt, or a pack assembled from the wrong scope — and it is the one leak this design exists
to make impossible.

## What to measure after a month

- **Re-done work**: tasks implemented twice. Should trend to zero; if it does not, the pack is not being
  read.
- **Resume cost**: pack size and how many `search` calls follow it. Growing pack + growing searches means
  compaction is behind.
- **Stale open tasks**: open with no activity for 14 days. Either they are blocked (add the reason) or
  they are dead (close them).
- **Claims without evidence**: `check` problems over time. Any recurrence means the workflow regressed,
  not the tool.
