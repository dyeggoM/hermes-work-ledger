import json, os, pathlib, shutil, sqlite3, subprocess, sys, tempfile, unittest

LEDGER = pathlib.Path(__file__).resolve().parents[1] / 'scripts' / 'ledger.py'


class Ledger(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.dir, True)

    def run_ledger(self, *args, scope='proj'):
        return subprocess.run([sys.executable, str(LEDGER), '--dir', self.dir, '--scope', scope, *args],
                              capture_output=True, text=True, cwd=self.dir)

    def ok(self, *args, scope='proj'):
        r = self.run_ledger(*args, scope=scope)
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        return r.stdout

    def db(self, scope='proj'):
        return sqlite3.connect(pathlib.Path(self.dir) / f'{scope}.db')

    def test_a_missing_ledger_is_never_created_silently(self):
        r = self.run_ledger('resume')
        self.assertEqual(r.returncode, 1)
        self.assertIn('no ledger', r.stderr)
        self.assertFalse((pathlib.Path(self.dir) / 'proj.db').exists())

    def test_request_then_resume_keeps_it_open(self):
        self.ok('init')
        tid = self.ok('request', 'Ship the purge-safe ledger').strip()
        self.assertRegex(tid, r'^T\d{4}$')
        pack = self.ok('resume')
        self.assertIn('Ship the purge-safe ledger', pack)
        self.assertIn('1 open', pack)

    def test_result_requires_evidence_and_closes_the_task(self):
        self.ok('init')
        tid = self.ok('request', 'Write the CLI').strip()
        self.assertNotEqual(self.run_ledger('result', '--task', tid, 'done').returncode, 0)
        self.ok('result', '--task', tid, 'CLI written', '--evidence', 'demo: 7 commands OK')
        status = json.loads(self.ok('status', '--json'))
        self.assertEqual(status['tasks']['done'], 1)
        self.assertEqual(status['tasks']['open'], 0)

    def test_a_done_task_without_a_result_event_is_a_problem(self):
        self.ok('init')
        tid = self.ok('request', 'Somehow marked done').strip()
        conn = self.db()
        conn.execute("UPDATE tasks SET status='done' WHERE id=?", (tid,))
        conn.commit(); conn.close()
        r = self.run_ledger('check')
        self.assertEqual(r.returncode, 1)
        self.assertIn('done with no result event', r.stdout)

    def test_failures_and_decisions_survive_the_pack(self):
        self.ok('init')
        self.ok('failure', 'Storing a paraphrase of the request', '--evidence', 'the exact wording is lost')
        self.ok('decision', 'One SQLite file per scope', '--why', 'two projects must not bleed')
        pack = self.ok('resume')
        self.assertIn('do not repeat', pack)
        self.assertIn('Storing a paraphrase', pack)
        self.assertIn('One SQLite file per scope', pack)
        self.assertIn('two projects must not bleed', pack)

    def test_secrets_are_redacted_on_write(self):
        self.ok('init')
        self.ok('note', 'deploy used token=ghp_' + 'a' * 24)
        pack = self.ok('resume')
        self.assertNotIn('ghp_', pack)
        self.assertIn('[redacted]', pack)
        self.ok('check')

    def test_two_scopes_do_not_leak_into_each_other(self):
        self.ok('init', scope='alpha')
        self.ok('request', 'sentinel-alpha-only', scope='alpha')
        self.ok('init', scope='beta')
        self.ok('request', 'sentinel-beta-only', scope='beta')
        self.assertNotIn('sentinel-alpha-only', self.ok('resume', scope='beta'))
        self.assertNotIn('sentinel-beta-only', self.ok('resume', scope='alpha'))

    def test_the_pack_is_bounded(self):
        self.ok('init')
        # The pack is bounded item-by-item (6 notes, 8 results, ...); the budget is the second line of
        # defence, for the case where a single entry is itself oversized.
        self.ok('note', 'one enormous entry ' + 'x' * 5000)
        # per-item caps keep a single oversized entry from dominating the pack
        self.assertLessEqual(len(self.ok('resume', '--budget', '4000')), 4100)
        # and the budget still truncates when the sections themselves do not fit
        pack = self.ok('resume', '--budget', '200')
        self.assertLessEqual(len(pack), 320)
        self.assertIn('truncated', pack)

    def test_many_entries_do_not_grow_the_pack_without_bound(self):
        self.ok('init')
        for i in range(40):
            self.ok('request', f'request {i} ' + 'y' * 100)
        pack = self.ok('resume', '--budget', '4000')
        self.assertLessEqual(len(pack), 4100)

    def test_search_finds_an_old_event(self):
        self.ok('init')
        self.ok('failure', 'the zipfile extractall allowed zip-slip')
        hits = self.ok('search', 'zipslip OR zip')
        self.assertIn('zip-slip', hits)

    def test_closing_a_task_as_blocked_keeps_it_visible(self):
        self.ok('init')
        tid = self.ok('request', 'Needs Diego to create the repo').strip()
        self.ok('close', '--task', tid, '--status', 'blocked', '--note', 'waiting on the GitHub repo')
        pack = self.ok('resume')
        self.assertIn('blocked', pack)
        self.assertIn('Needs Diego', pack)


if __name__ == '__main__':
    unittest.main()
