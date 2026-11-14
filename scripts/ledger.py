#!/usr/bin/env python3
"""Work ledger: an append-only record of what was asked and what was done, per scope.

The point is not bookkeeping. It is that the context window can be thrown away — `/new`, a
compaction, a handover, a crash — and the next session still knows the open requests, the decisions
that constrain them, what was already tried and failed, and where the evidence lives.

Storage: one SQLite file per scope (`<dir>/<scope>.db`), FTS5 over the event log, append-only events
plus tasks derived from them. No dependencies beyond the standard library.
"""
import argparse, datetime, hashlib, json, os, re, sqlite3, sys
from pathlib import Path

try:
    import fcntl
except ImportError:                                                    # windows
    fcntl = None

VERSION = '0.1.0'
KINDS = ('request', 'action', 'result', 'failure', 'decision', 'note')
STATUSES = ('open', 'in_progress', 'done', 'blocked', 'dropped')

SECRET_PATTERNS = (
    (re.compile(r'(?i)\b(api[_-]?key|secret|token|password|passwd|pwd)\b(\s*[=:]\s*)(\S+)'), r'\1\2[redacted]'),
    (re.compile(r'\b(sk-[A-Za-z0-9_-]{16,}|ghp_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}'
                r'|xox[baprs]-[0-9A-Za-z-]{10,}|AKIA[0-9A-Z]{16})\b'), '[redacted]'),
    (re.compile(r'-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----', re.S), '[redacted]'),
)

SCHEMA = """
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS tasks(
    id TEXT PRIMARY KEY, title TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL, closed_at TEXT, note TEXT NOT NULL DEFAULT '');
CREATE TABLE IF NOT EXISTS events(
    id INTEGER PRIMARY KEY, at TEXT NOT NULL, kind TEXT NOT NULL, task_id TEXT,
    text TEXT NOT NULL, evidence TEXT NOT NULL DEFAULT '', why TEXT NOT NULL DEFAULT '',
    actor TEXT NOT NULL DEFAULT 'agent');
CREATE VIRTUAL TABLE IF NOT EXISTS events_fts USING fts5(text, evidence, why);
CREATE INDEX IF NOT EXISTS events_task ON events(task_id);
"""


def now():
    return datetime.datetime.now(datetime.timezone.utc).replace(microsecond=0).isoformat()


def redact(text):
    for pattern, repl in SECRET_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def scope_dir(value, directory):
    return Path(directory or '.ledger').resolve() / f'{value}.db'


def connect(path, create=True):
    if not create and not Path(path).is_file():
        return None
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    conn.executescript(SCHEMA)
    conn.execute("INSERT OR IGNORE INTO meta VALUES('created_at', ?)", (now(),))
    conn.commit()
    return conn


class Lock:
    """One writer per scope: the agent, a background worker and a CLI must not interleave."""

    def __init__(self, path):
        self.path = Path(path)
        self.fh = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.fh = open(self.path, 'a+')
        if fcntl:
            fcntl.flock(self.fh, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc):
        if fcntl and self.fh:
            fcntl.flock(self.fh, fcntl.LOCK_UN)
        if self.fh:
            self.fh.close()


def task_id(conn, title):
    n = conn.execute('SELECT COUNT(*) FROM tasks').fetchone()[0] + 1
    base = f'T{n:04d}'
    return base if not conn.execute('SELECT 1 FROM tasks WHERE id=?', (base,)).fetchone() else \
        base + '-' + hashlib.sha256(title.encode()).hexdigest()[:4]


def add_event(conn, kind, text, task=None, evidence='', why='', actor='agent', at=None):
    at = at or now()
    cur = conn.execute('INSERT INTO events(at,kind,task_id,text,evidence,why,actor) VALUES(?,?,?,?,?,?,?)',
                       (at, kind, task, redact(text), redact(evidence), redact(why), actor))
    conn.execute('INSERT INTO events_fts(rowid,text,evidence,why) VALUES(?,?,?,?)',
                 (cur.lastrowid, redact(text), redact(evidence), redact(why)))
    return cur.lastrowid


def open_task(conn, title, actor='user'):
    tid = task_id(conn, title)
    conn.execute('INSERT INTO tasks(id,title,status,created_at) VALUES(?,?,?,?)', (tid, redact(title), 'open', now()))
    add_event(conn, 'request', title, task=tid, actor=actor)
    return tid


def get_task(conn, tid):
    row = conn.execute('SELECT * FROM tasks WHERE id=?', (tid,)).fetchone()
    if not row:
        raise SystemExit(f'ledger: unknown task {tid}')
    return row


def set_status(conn, tid, status, note=''):
    conn.execute('UPDATE tasks SET status=?, closed_at=?, note=? WHERE id=?',
                 (status, now() if status in ('done', 'dropped') else None, redact(note), tid))


def recent_tasks(conn, statuses, limit=40):
    marks = ','.join('?' * len(statuses))
    return conn.execute(
        f'''SELECT t.*, (SELECT at FROM events e WHERE e.task_id=t.id ORDER BY e.id DESC LIMIT 1) AS last_at,
                   (SELECT text FROM events e WHERE e.task_id=t.id ORDER BY e.id DESC LIMIT 1) AS last_text
            FROM tasks t WHERE t.status IN ({marks}) ORDER BY t.created_at DESC LIMIT ?''',
        (*statuses, limit)).fetchall()


def global_events(conn, kinds, limit):
    marks = ','.join('?' * len(kinds))
    return conn.execute(f'SELECT * FROM events WHERE kind IN ({marks}) ORDER BY id DESC LIMIT ?',
                        (*kinds, limit)).fetchall()


def pack(conn, scope, budget=4000):
    """The context a fresh session reads instead of the conversation it lost."""
    open_tasks = recent_tasks(conn, ('open', 'in_progress', 'blocked'))
    done_tasks = recent_tasks(conn, ('done',), limit=5)
    decisions = global_events(conn, ('decision',), 6)
    failures = global_events(conn, ('failure',), 5)
    actions = global_events(conn, ('result', 'action'), 8)
    notes = global_events(conn, ('note',), 6)

    out = [f'# Ledger · {scope}', '',
           f'{len(open_tasks)} open · {len(done_tasks)} recently closed · pack {budget} chars', '',
           '## Open work']
    if not open_tasks:
        out.append('- (nothing open)')
    for t in open_tasks:
        out += [f'- {t["id"]} [{t["status"]}] {t["title"]}',
                f'  last {t["last_at"][:16]}: {(t["last_text"] or "")[:180]}']
    if decisions:
        out += ['', '## Decisions that constrain the next step']
        for d in decisions:
            out.append(f'- {d["at"][:10]} {d["text"][:160]}' + (f' — why: {d["why"][:120]}' if d['why'] else ''))
    if failures:
        out += ['', '## Already tried and failed — do not repeat']
        for f in failures:
            out.append(f'- {f["at"][:10]} {f["text"][:160]}' + (f' ({f["evidence"][:80]})' if f['evidence'] else ''))
    if actions:
        out += ['', '## Recent work, with its evidence']
        for a in actions:
            ev = f' · {a["evidence"][:100]}' if a['evidence'] else ' · NO EVIDENCE'
            out.append(f'- {a["at"][:10]} {a["text"][:150]}{ev}')
    if notes:
        out += ['', '## Notes worth keeping']
        for n in notes:
            out.append(f'- {n["at"][:10]} {n["text"][:150]}')
    if done_tasks:
        out += ['', '## Recently closed']
        for t in done_tasks:
            out.append(f'- {t["id"]} {t["title"][:120]}')

    text = '\n'.join(out) + '\n'
    if len(text) > budget:
        text = text[:budget].rsplit('\n', 1)[0] + f'\n… truncated at {budget} chars, run `search` for the rest\n'
    return text


def check(conn, stale_days=14):
    problems, warnings = [], []
    for row in conn.execute("SELECT id FROM tasks WHERE status='done'"):
        ev = conn.execute("SELECT 1 FROM events WHERE task_id=? AND kind='result' AND evidence<>''", (row['id'],)).fetchone()
        if not ev:
            problems.append(f'{row["id"]} is done with no result event carrying evidence')
    for row in conn.execute("SELECT id,text FROM events WHERE kind='result' AND evidence=''"):
        problems.append(f'event {row["id"]} is a result with empty evidence: {row["text"][:60]}')
    cutoff = (datetime.datetime.now(datetime.timezone.utc) - datetime.timedelta(days=stale_days)).isoformat()
    for row in conn.execute('SELECT id,title FROM tasks WHERE status IN (\'open\',\'in_progress\')').fetchall():
        last = conn.execute('SELECT MAX(at) FROM events WHERE task_id=?', (row['id'],)).fetchone()[0]
        if last and last < cutoff:
            warnings.append(f'{row["id"]} has had no activity since {last[:10]}: {row["title"][:60]}')
    for row in conn.execute('SELECT id,text,evidence,why FROM events'):
        # The marker written by redact() is not a secret: strip it before scanning, or every redacted
        # event reports itself.
        blob = ' '.join(filter(None, (row['text'], row['evidence'], row['why']))).replace('[redacted]', '')
        for pattern, _ in SECRET_PATTERNS:
            if pattern.search(blob):
                problems.append(f'event {row["id"]} looks like it carries a secret; re-write it redacted')
                break
    missing = conn.execute('SELECT COUNT(*) FROM events WHERE id NOT IN (SELECT rowid FROM events_fts)').fetchone()[0]
    if missing:
        warnings.append(f'{missing} event(s) missing from the search index; rebuild with `reindex`')
    return problems, warnings


def main():
    ap = argparse.ArgumentParser(prog='ledger', description='Append-only work memory: what was asked, what was done.')
    ap.add_argument('--dir', default=None, help='ledger directory (default: .ledger)')
    ap.add_argument('--scope', default=None, help='scope name (default: the directory name)')
    sub = ap.add_subparsers(dest='cmd', required=True)

    sub.add_parser('init', help='create the ledger for this scope')
    p = sub.add_parser('request', help='record something that was asked'); p.add_argument('text'); p.add_argument('--actor', default='user')
    p = sub.add_parser('action', help='record work in progress'); p.add_argument('text'); p.add_argument('--task'); p.add_argument('--evidence', default='')
    p = sub.add_parser('result', help='record finished work; evidence is required'); p.add_argument('text'); p.add_argument('--task'); p.add_argument('--evidence', required=True)
    p = sub.add_parser('failure', help='record an attempt that failed, so it is not repeated'); p.add_argument('text'); p.add_argument('--task'); p.add_argument('--evidence', default='')
    p = sub.add_parser('decision', help='record a decision and why'); p.add_argument('text'); p.add_argument('--why', default=''); p.add_argument('--task')
    p = sub.add_parser('note', help='anything else worth keeping'); p.add_argument('text'); p.add_argument('--task')
    p = sub.add_parser('close', help='close or block a task'); p.add_argument('--task', required=True); p.add_argument('--status', choices=['blocked', 'dropped'], required=True); p.add_argument('--note', default='')
    p = sub.add_parser('resume', help='print the context pack a fresh session should read'); p.add_argument('--budget', type=int, default=4000)
    p = sub.add_parser('search', help='full-text search over the log'); p.add_argument('query'); p.add_argument('--limit', type=int, default=10)
    p = sub.add_parser('status', help='one-screen state, JSON with --json'); p.add_argument('--json', action='store_true')
    sub.add_parser('check', help='integrity: results without evidence, secrets, stale work, index gaps')
    sub.add_parser('reindex', help='rebuild the search index from the event log')

    args = ap.parse_args()
    scope = args.scope or Path.cwd().name
    db = scope_dir(scope, args.dir)

    if args.cmd == 'status' and not db.is_file():
        print(json.dumps({'scope': scope, 'exists': False, 'open_tasks': 0}) if args.json else
              f'no ledger for scope {scope} ({db}); run: ledger init')
        return 0

    create = args.cmd == 'init'
    conn = connect(db, create=create)
    if conn is None:
        raise SystemExit(f'ledger: no ledger for scope {scope} at {db}; run `ledger init` (a missing ledger is never created silently)')

    try:
        with Lock(Path(db).with_suffix('.lock')):
            if args.cmd == 'init':
                add_event(conn, 'note', 'ledger initialised', actor='system')
                conn.commit()
                print(f'{db}')
            elif args.cmd == 'request':
                tid = open_task(conn, args.text, actor=args.actor); conn.commit(); print(tid)
            elif args.cmd == 'action':
                if args.task: get_task(conn, args.task); set_status(conn, args.task, 'in_progress')
                add_event(conn, 'action', args.text, task=args.task, evidence=args.evidence); conn.commit(); print('ok')
            elif args.cmd == 'result':
                if args.task: get_task(conn, args.task); set_status(conn, args.task, 'done')
                add_event(conn, 'result', args.text, task=args.task, evidence=args.evidence); conn.commit(); print('ok')
            elif args.cmd == 'failure':
                if args.task: get_task(conn, args.task)
                add_event(conn, 'failure', args.text, task=args.task, evidence=args.evidence); conn.commit(); print('ok')
            elif args.cmd == 'decision':
                if args.task: get_task(conn, args.task)
                add_event(conn, 'decision', args.text, task=args.task, why=args.why); conn.commit(); print('ok')
            elif args.cmd == 'note':
                if args.task: get_task(conn, args.task)
                add_event(conn, 'note', args.text, task=args.task); conn.commit(); print('ok')
            elif args.cmd == 'close':
                get_task(conn, args.task); set_status(conn, args.task, args.status, args.note)
                add_event(conn, 'note', f'{args.task} -> {args.status}: {args.note}', task=args.task); conn.commit(); print(args.task, args.status)
            elif args.cmd == 'resume':
                print(pack(conn, scope, args.budget), end='')
            elif args.cmd == 'search':
                rows = conn.execute('''SELECT e.id, e.at, e.kind, e.task_id, e.text, e.evidence
                                       FROM events_fts f JOIN events e ON e.id=f.rowid
                                       WHERE events_fts MATCH ? ORDER BY rank LIMIT ?''',
                                    (args.query, args.limit)).fetchall()
                if not rows:
                    print('no match')
                for r in rows:
                    ev = f' · {r["evidence"][:120]}' if r['evidence'] else ''
                    print(f'{r["at"][:16]} [{r["kind"]}] {r["text"][:200]}{ev}')
            elif args.cmd == 'status':
                counts = {s: conn.execute('SELECT COUNT(*) FROM tasks WHERE status=?', (s,)).fetchone()[0] for s in STATUSES}
                payload = {'scope': scope, 'exists': True, 'tasks': counts,
                           'events': conn.execute('SELECT COUNT(*) FROM events').fetchone()[0],
                           'last_event': conn.execute('SELECT MAX(at) FROM events').fetchone()[0]}
                print(json.dumps(payload, indent=2) if args.json else
                      f'{scope}: ' + ', '.join(f'{k}={v}' for k, v in counts.items()) + f' · {payload["events"]} events')
            elif args.cmd == 'check':
                problems, warnings = check(conn)
                for w in warnings: print(f'warn: {w}')
                for p in problems: print(f'problem: {p}')
                print(f'{len(problems)} problem(s), {len(warnings)} warning(s)')
                return 1 if problems else 0
            elif args.cmd == 'reindex':
                conn.execute('DELETE FROM events_fts')
                for r in conn.execute('SELECT id,text,evidence,why FROM events'):
                    conn.execute('INSERT INTO events_fts(rowid,text,evidence,why) VALUES(?,?,?,?)',
                                 (r['id'], r['text'], r['evidence'], r['why']))
                conn.commit(); print('reindexed')
    finally:
        conn.close()
    return 0


if __name__ == '__main__':
    sys.exit(main())
