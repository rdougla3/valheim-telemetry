"""Persistent death/respawn history and online-only survival streaks.

Historical reset signals are inferred deaths, not character lifetime counters.
World-clock interpolation is weighted only over occupied server intervals.
"""
from bisect import bisect_right
from collections import defaultdict
from datetime import datetime
import json
import math
import re

CHARACTER = re.compile(r'Got character ZDOID from (.+?)\s*:\s*(-?\d+):(\d+)')
SLEEP = re.compile(r'Time ([\d.]+), day:(\d+)\s+nextm:([\d.]+)\s+skipspeed:([\d.]+)')


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS combat_signals(
            cursor TEXT PRIMARY KEY, ts REAL NOT NULL, kind TEXT NOT NULL, payload TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS combat_signals_time ON combat_signals(ts);
        CREATE TABLE IF NOT EXISTS combat_clock(
            ts REAL PRIMARY KEY, world_seconds REAL NOT NULL, day_length REAL NOT NULL,
            source TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS combat_deaths(
            cursor TEXT PRIMARY KEY, ts REAL NOT NULL, session_id INTEGER NOT NULL,
            creator_id TEXT NOT NULL, name TEXT NOT NULL, source TEXT NOT NULL);
        CREATE INDEX IF NOT EXISTS combat_deaths_player ON combat_deaths(creator_id,ts);
        CREATE TABLE IF NOT EXISTS combat_stats(
            creator_id TEXT PRIMARY KEY, name TEXT NOT NULL, deaths INTEGER NOT NULL,
            current_days REAL NOT NULL, longest_days REAL NOT NULL, online_days REAL NOT NULL,
            last_death REAL, first_recorded REAL, updated_at REAL NOT NULL);
    ''')
    db.execute("INSERT OR IGNORE INTO metadata VALUES('combat_clock_mode','world')")


def capture_entry(db, entry):
    message = entry.get('MESSAGE', '')
    if not isinstance(message, str): return
    ts = int(entry['__REALTIME_TIMESTAMP']) / 1e6
    cursor = entry['__CURSOR']
    if match := CHARACTER.search(message):
        name, peer, obj = match.groups()
        payload = {'name': name, 'peer': peer, 'object': int(obj)}
        kind = 'character'
    elif 'VALHEIM_IDENTITY ' in message:
        try: payload = json.loads(message.split('VALHEIM_IDENTITY ', 1)[1])
        except (ValueError, IndexError): return
        kind = 'identity'
    elif match := SLEEP.search(message):
        current, day, target, speed = map(float, match.groups())
        if not all(math.isfinite(n) for n in (current, target, speed)): return
        # Game's sleep log states the world clock, target and 12-second skip rate.
        # The target timestamp is estimated; subsequent saved/live samples refine it.
        payload = {'current': current, 'target': target, 'duration': 12.0, 'day': day}
        kind = 'sleep'
    else:
        return
    db.execute('INSERT OR IGNORE INTO combat_signals VALUES(?,?,?,?)',
               (cursor, ts, kind, json.dumps(payload)))


def add_clock(db, ts, seconds, length=1800, source='live'):
    if all(math.isfinite(v) for v in (ts, seconds, length)) and seconds >= 0 and length > 0:
        db.execute('INSERT OR IGNORE INTO combat_clock VALUES(?,?,?,?)',
                   (ts, seconds, length, source))


def seed_snapshot_clocks(db):
    for row in db.execute('SELECT captured,payload FROM snapshots'):
        w = json.loads(row['payload'])
        # Restored/imported files can have old mtimes; do not treat them as fresh observations.
        if 0 <= row['captured'] - w['saved_at'] <= 600:
            add_clock(db, w['saved_at'], w['world_seconds'], source='saved')


def backfill(db, entries, now):
    if db.execute("SELECT 1 FROM metadata WHERE key='combat_backfill_v1'").fetchone():
        return False
    count = 0
    first = last = None
    for entry in entries:
        capture_entry(db, entry)
        ts = int(entry['__REALTIME_TIMESTAMP']) / 1e6
        first = ts if first is None else min(first, ts)
        last = ts if last is None else max(last, ts)
        count += 1
    seed_snapshot_clocks(db)
    record = {'completed_at': now, 'first_log': first, 'last_log': last, 'entries': count}
    db.execute("INSERT INTO metadata VALUES('combat_backfill_v1',?)", (json.dumps(record),))
    return True


def merge_intervals(intervals):
    merged = []
    for start, end in sorted(intervals):
        if end <= start: continue
        if merged and start <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(end, merged[-1][1]))
        else: merged.append((start, end))
    return merged


class WorldClock:
    def __init__(self, samples, occupied, mode='world', day_length=1800):
        self.mode = mode
        self.day_length = day_length
        self.occupied = merge_intervals(occupied)
        self.samples = sorted(samples, key=lambda s: s[0])
        self.times = [s[0] for s in self.samples]
        self.bands = []
        for a, b in zip(self.samples, self.samples[1:]):
            online = self.occupied_seconds(a[0], b[0])
            delta = max(0, b[1] - a[1])
            # A rolled-back world never creates negative survival time.
            rate = delta / max(1, b[2]) / online if online else 0
            self.bands.append(rate)

    def occupied_seconds(self, start, end):
        return sum(max(0, min(end, b) - max(start, a)) for a, b in self.occupied)

    def days(self, start, end):
        if end <= start: return 0.0
        if self.mode == 'playtime' or len(self.samples) < 2:
            return (end - start) / self.day_length
        total = 0.0
        pos = start
        while pos < end:
            i = bisect_right(self.times, pos) - 1
            if i < 0:
                stop = min(end, self.times[0])
                rate = 1 / self.day_length
            elif i >= len(self.bands):
                stop = end
                rate = 1 / self.day_length
            else:
                stop = min(end, self.times[i+1])
                rate = self.bands[i]
            if stop <= pos: break
            total += (stop - pos) * rate
            pos = stop
        return total


def _resolve_session(session, identities, identity_signals):
    peer = str(session['peer'])
    candidates = {str(p['creator_id']) for ts, p in identity_signals
                  if str(p.get('peer')) == peer and p.get('creator_id')
                  and ts >= session['started'] - 1
                  and (session['ended'] is None or ts <= session['ended'] + 1)}
    if len(candidates) == 1: return candidates.pop()
    candidates = {i['creator_id'] for i in identities
                  if session['platform_id'] and i['platform_id'] == session['platform_id']}
    if len(candidates) == 1: return candidates.pop()
    candidates = {i['creator_id'] for i in identities if i['name'] == session['name']}
    return candidates.pop() if len(candidates) == 1 else None


def refresh(db, live, now):
    sessions = [dict(s) for s in db.execute('SELECT * FROM sessions ORDER BY started') if str(s['peer']) != '0']
    identities = [dict(i) for i in db.execute('SELECT * FROM identities')]
    signals = [(r['cursor'], r['ts'], r['kind'], json.loads(r['payload']))
               for r in db.execute('SELECT * FROM combat_signals ORDER BY ts,cursor')]
    identity_signals = [(ts, p) for _, ts, kind, p in signals if kind == 'identity']
    chars_by_peer = defaultdict(list)
    zeros_by_name = defaultdict(list)
    for cursor, ts, kind, p in signals:
        if kind == 'sleep':
            add_clock(db, ts, p['current'], source='sleep-start')
            if ts + p['duration'] <= now:
                add_clock(db, ts + p['duration'], p['target'], source='sleep-target-estimate')
        elif kind == 'character':
            if int(p['peer']) == 0:
                zeros_by_name[p['name']].append((cursor, ts, p))
            else: chars_by_peer[str(p['peer'])].append((cursor, ts, p))
    if live and live.get('world_seconds') is not None:
        stamp = datetime.fromisoformat(live['observed_at'].replace('Z', '+00:00')).timestamp()
        add_clock(db, stamp, float(live['world_seconds']), float(live.get('day_length', 1800)))
    # A new saved clock is useful even before the next live sample, and survives restarts.
    row = db.execute('SELECT captured,payload FROM snapshots ORDER BY captured DESC LIMIT 1').fetchone()
    if row:
        w = json.loads(row['payload'])
        if 0 <= row['captured'] - w['saved_at'] <= 600:
            add_clock(db, w['saved_at'], w['world_seconds'], source='saved')
    spans = defaultdict(list)
    first_seen = {}
    unresolved = 0
    for s in sessions:
        creator = _resolve_session(s, identities, identity_signals)
        if not creator:
            unresolved += 1
            continue
        first_seen[creator] = min(first_seen.get(creator, s['started']), s['started'])
        end = min(s['ended'] if s['ended'] is not None else now, now)
        if end <= s['started']: continue
        chars = [(c, t, p) for c, t, p in chars_by_peer[str(s['peer'])]
                 if s['started'] - 1 <= t <= end]
        zeros = []
        for c, t, p in zeros_by_name[s['name']]:
            if not s['started'] <= t <= end: continue
            # Never attribute a name-only death when two online characters share the name.
            same_name = [x for x in sessions if x['name'] == s['name']
                         and x['started'] <= t <= (x['ended'] if x['ended'] is not None else now)]
            if len(same_name) == 1: zeros.append((c, t, p))
        active = True
        pos = s['started']
        for cursor, ts, p in sorted(chars + zeros, key=lambda e: (e[1], e[0])):
            if int(p['peer']) == 0:
                if not active: continue
                if ts > pos: spans[creator].append((pos, ts))
                active = False
                db.execute('INSERT OR IGNORE INTO combat_deaths VALUES(?,?,?,?,?,?)',
                           (cursor, ts, s['id'], creator, s['name'], 'server death/respawn transition'))
            elif not active:
                active = True
                pos = max(ts, s['started'])
        if active and end > pos: spans[creator].append((pos, end))
    mode_row = db.execute("SELECT value FROM metadata WHERE key='combat_clock_mode'").fetchone()
    mode = mode_row['value'] if mode_row else 'world'
    clock = WorldClock([(r['ts'], r['world_seconds'], r['day_length'])
                        for r in db.execute('SELECT * FROM combat_clock ORDER BY ts')],
                       [(s['started'], min(s['ended'] if s['ended'] is not None else now, now)) for s in sessions], mode)
    deaths = defaultdict(list)
    for r in db.execute('SELECT * FROM combat_deaths ORDER BY ts,cursor'):
        deaths[r['creator_id']].append(r['ts'])
    for identity in identities:
        creator = identity['creator_id']
        current = longest = total = 0.0
        times = deaths[creator]
        i = 0
        for start, end in merge_intervals(spans[creator]):
            while i < len(times) and times[i] <= start:
                longest = max(longest, current)
                current = 0.0
                i += 1
            pos = start
            while i < len(times) and times[i] < end:
                days = clock.days(pos, times[i])
                current += days; total += days
                longest = max(longest, current)
                current = 0.0; pos = times[i]; i += 1
            days = clock.days(pos, end)
            current += days; total += days
            longest = max(longest, current)
        if i < len(times):
            longest = max(longest, current)
            current = 0.0
        db.execute('''INSERT OR REPLACE INTO combat_stats VALUES(?,?,?,?,?,?,?,?,?)''',
                   (creator, identity['name'], len(times), current, max(longest, current), total,
                    times[-1] if times else None, first_seen.get(creator), now))
    db.execute("INSERT OR REPLACE INTO metadata VALUES('combat_unresolved_sessions',?)", (str(unresolved),))


def summary(db):
    row = db.execute("SELECT value FROM metadata WHERE key='combat_backfill_v1'").fetchone()
    coverage = json.loads(row['value']) if row else {}
    mode = db.execute("SELECT value FROM metadata WHERE key='combat_clock_mode'").fetchone()
    players = [dict(r) for r in db.execute('SELECT * FROM combat_stats WHERE first_recorded IS NOT NULL ORDER BY current_days DESC, longest_days DESC')]
    for p in players:
        for key in ('current_days', 'longest_days', 'online_days'): p[key] = round(p[key], 3)
    return {'players': players, 'total_deaths': sum(p['deaths'] for p in players),
            'coverage_start': coverage.get('first_log'), 'clock_mode': mode['value'] if mode else 'world',
            'historical_estimates': True}
