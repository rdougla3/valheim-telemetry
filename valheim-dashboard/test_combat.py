import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
import combat


class CombatTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(app, 'DB', Path(self.temp.name) / 'combat.sqlite3')
        self.db_patch.start()
        app.initialize()
        self.db = app.connect()
        self.db.execute('DELETE FROM identities')
        for creator, name in [('a', 'Alpha'), ('b', 'Beta')]:
            self.db.execute('INSERT INTO identities VALUES(?,?,?,?,?,?)',
                            (creator, name, creator, 0, 0, 'test'))
    def tearDown(self):
        self.db.close(); self.db_patch.stop(); self.temp.cleanup()
    def session(self, name, peer, start, end, account=None):
        account = account or ('a' if name == 'Alpha' else 'b')
        self.db.execute('INSERT INTO sessions(platform_id,name,started,ended,peer) VALUES(?,?,?,?,?)',
                        (account, name, start, end, peer))
    def signal(self, cursor, ts, message):
        entry = {'__CURSOR': cursor, '__REALTIME_TIMESTAMP': str(round(ts*1e6)), 'MESSAGE': message}
        combat.capture_entry(self.db, entry)
        return entry
    def clock(self, *samples):
        for ts, seconds in samples: combat.add_clock(self.db, ts, seconds)
    def stats(self, now, creator='a'):
        combat.refresh(self.db, None, now)
        return dict(self.db.execute('SELECT * FROM combat_stats WHERE creator_id=?', (creator,)).fetchone())
    def test_other_players_advancing_world_while_offline_do_not_extend_streak(self):
        self.session('Alpha', '1', 0, 100)
        self.session('Beta', '2', 100, 10000)
        self.session('Alpha', '3', 10000, 10100)
        self.clock((0, 0), (10100, 10100))
        self.assertAlmostEqual(self.stats(10100)['current_days'], 200/1800)
        self.assertAlmostEqual(self.stats(10100, 'b')['current_days'], 9900/1800)
    def test_sleep_skip_is_shared_only_with_players_online_during_skip(self):
        self.session('Alpha', '1', 0, 120)
        self.session('Beta', '2', 20, 120)
        self.clock((0, 0), (10, 10), (20, 1210), (120, 1310))
        self.assertAlmostEqual(self.stats(120)['current_days'], 1310/1800)
        self.assertAlmostEqual(self.stats(120, 'b')['current_days'], 100/1800)
    def test_deaths_reset_current_preserve_best_and_survive_replay(self):
        self.session('Alpha', '1', 0, 1800)
        self.session('Alpha', '2', 3600, 7200)
        self.clock((0, 0), (7200, 5400))
        self.signal('d1', 900, 'Got character ZDOID from Alpha : 0:0')
        self.signal('r1', 910, 'Got character ZDOID from Alpha : 1:5')
        self.signal('d2', 5000, 'Got character ZDOID from Alpha : 0:0')
        self.signal('d2-repeat', 5001, 'Got character ZDOID from Alpha : 0:0')
        self.signal('r2', 5010, 'Got character ZDOID from Alpha : 2:9')
        result = self.stats(7200)
        self.assertEqual(result['deaths'], 2)
        self.assertAlmostEqual(result['current_days'], 2190/1800)
        self.assertAlmostEqual(result['longest_days'], 2290/1800)
        self.assertEqual(self.stats(9000)['deaths'], 2)
        self.assertAlmostEqual(self.stats(9000)['current_days'], result['current_days'])
    def test_initial_none_and_logout_do_not_create_deaths(self):
        self.session('Alpha', '1', 10, 100)
        self.signal('initial', 1, 'Got character ZDOID from Alpha : 0:0')
        self.signal('spawn', 10, 'Got character ZDOID from Alpha : 1:1')
        self.signal('logout', 100, 'Closing socket a')
        self.assertEqual(self.stats(1000)['deaths'], 0)
        self.assertAlmostEqual(self.stats(1000)['current_days'], 90/1800)
    def test_death_without_respawn_stays_at_zero_until_next_login(self):
        self.session('Alpha', '1', 0, 100)
        self.signal('death', 50, 'Got character ZDOID from Alpha : 0:0')
        self.assertEqual(self.stats(10000)['current_days'], 0)
        self.session('Alpha', '2', 10000, 10100)
        self.assertAlmostEqual(self.stats(10100)['current_days'], 100/1800)
    def test_live_sample_is_persisted_and_advances_open_session(self):
        self.session('Alpha', '1', 100, None)
        self.clock((100, 1800))
        live = {'observed_at': '1970-01-01T00:03:20+00:00', 'world_seconds': 3600, 'day_length': 1800}
        combat.refresh(self.db, live, 200)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_clock').fetchone()[0], 2)
        self.assertAlmostEqual(self.stats(200)['current_days'], 1)
    def test_backfill_runs_once_and_signal_cursor_is_idempotent(self):
        self.session('Alpha', '1', 0, 100)
        entry = self.signal('d', 50, 'Got character ZDOID from Alpha : 0:0')
        self.assertTrue(combat.backfill(self.db, [entry], 100))
        self.assertFalse(combat.backfill(self.db, [entry], 200))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM combat_signals').fetchone()[0], 1)
        self.assertEqual(self.stats(100)['deaths'], 1)
    def test_same_character_overlapping_sessions_are_not_double_counted(self):
        self.session('Alpha', '1', 0, 3600)
        self.session('Alpha', '2', 1800, 5400)
        self.clock((0, 0), (5400, 5400))
        self.assertAlmostEqual(self.stats(5400)['current_days'], 3)
    def test_clock_rollback_never_subtracts_or_creates_negative_days(self):
        self.session('Alpha', '1', 0, 200)
        self.clock((0, 2000), (100, 1900), (200, 2000))
        self.assertAlmostEqual(self.stats(200)['current_days'], 100/1800)


if __name__ == '__main__': unittest.main()
