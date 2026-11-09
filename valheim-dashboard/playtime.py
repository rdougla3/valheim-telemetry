"""Durable cumulative playtime; historical journal replay preserves corrected sessions."""
from collections import defaultdict
import json
import re
import combat


def initialize(db):
    db.executescript('''
        CREATE TABLE IF NOT EXISTS playtime_totals(
          creator_id TEXT PRIMARY KEY, seconds REAL NOT NULL, sessions INTEGER NOT NULL,
          longest_seconds REAL NOT NULL, first_seen REAL, last_seen REAL, updated REAL NOT NULL);
        CREATE TABLE IF NOT EXISTS playtime_session_links(
          session_id INTEGER PRIMARY KEY, creator_id TEXT, source TEXT NOT NULL);
    ''')


def backfill(db, entries, now, normalize):
    if db.execute("SELECT 1 FROM metadata WHERE key='playtime_backfill_v1'").fetchone(): return
    # Reuse corrected durable intervals whenever available. Journal replay fills missing starts;
    # it does not undo presence-reconciled ends or infer logout from a character's destruction.
    active = {}; handshakes = []; first = last = None; added = 0
    for e in entries:
        ts = int(e['__REALTIME_TIMESTAMP']) / 1e6; msg = e.get('MESSAGE', '')
        first = ts if first is None else min(first, ts); last = ts
        if not isinstance(msg, str): continue
        if m := re.search(r'Got handshake from client (\S+)', msg):
            handshakes.append((ts, normalize(m[1])))
        elif m := combat.CHARACTER.search(msg):
            name, peer, obj = m.groups()
            if int(peer) == 0 or peer in active: continue
            match = db.execute('SELECT * FROM sessions WHERE peer=? AND ABS(started-?)<2 ORDER BY started LIMIT 1', (peer, ts)).fetchone()
            if match: active[peer] = dict(match)
            else:
                candidates = [h for h in handshakes if 0 <= ts-h[0] < 180]
                account = candidates[0][1] if len(candidates) == 1 else ''
                cur = db.execute('INSERT INTO sessions(platform_id,name,started,source,peer) VALUES(?,?,?,?,?)',
                                 (account, name, ts, 'journal playtime backfill', peer))
                active[peer] = dict(id=cur.lastrowid, platform_id=account, ended=None, name=name)
                added += 1
        elif 'VALHEIM_IDENTITY ' in msg:
            try: p = json.loads(msg.split('VALHEIM_IDENTITY ',1)[1])
            except ValueError: continue
            if str(p.get('peer')) in active:
                s = active[str(p['peer'])]; s['platform_id'] = normalize(p.get('platform_id'))
                db.execute('UPDATE sessions SET platform_id=? WHERE id=?', (s['platform_id'], s['id']))
        elif m := re.search(r'Closing socket (\S+)', msg):
            account = normalize(m[1])
            for peer,s in list(active.items()):
                if s['platform_id'] == account:
                    db.execute('UPDATE sessions SET ended=COALESCE(ended,?) WHERE id=?', (ts,s['id']))
                    active.pop(peer)
        elif re.search(r'Stopping valheim.service|Stopped valheim.service|Main process exited|Starting valheim.service|Started valheim.service|VALHEIM_SERVER_START|Game - OnApplicationQuit', msg):
            for s in active.values(): db.execute('UPDATE sessions SET ended=COALESCE(ended,?) WHERE id=?', (ts,s['id']))
            active.clear(); handshakes.clear()
        # A durable end can predate the next CharacterID without a matching socket log (PlayFab).
        for peer,s in list(active.items()):
            if s['ended'] is not None and s['ended'] <= ts: active.pop(peer)
    db.execute("INSERT INTO metadata VALUES('playtime_backfill_v1',?)", (json.dumps(dict(completed_at=now, first_log=first, last_log=last, added_sessions=added)),))


def refresh(db, now):
    identities = [dict(r) for r in db.execute('SELECT * FROM identities')]
    signals = [(r['ts'], json.loads(r['payload'])) for r in db.execute("SELECT ts,payload FROM combat_signals WHERE kind='identity'")]
    spans = defaultdict(list); unresolved = 0
    for row in db.execute('SELECT * FROM sessions ORDER BY started').fetchall():
        s = dict(row)
        if str(s['peer']) == '0': continue
        creator = combat._resolve_session(s, identities, signals)
        db.execute('INSERT OR REPLACE INTO playtime_session_links VALUES(?,?,?)', (s['id'],creator,'peer identity, unique account, or unique name'))
        if not creator: unresolved += 1; continue
        end = min(now, s['ended'] if s['ended'] is not None else now)
        if end > s['started']: spans[creator].append((s['started'],end))
    for i in identities:
        intervals = combat.merge_intervals(spans[i['creator_id']])
        db.execute('INSERT OR REPLACE INTO playtime_totals VALUES(?,?,?,?,?,?,?)',
                   (i['creator_id'],sum(b-a for a,b in intervals),len(intervals),max((b-a for a,b in intervals),default=0),
                    intervals[0][0] if intervals else None, intervals[-1][1] if intervals else None,now))
    db.execute("INSERT OR REPLACE INTO metadata VALUES('playtime_unresolved',?)", (str(unresolved),))


def summary(db):
    row = db.execute("SELECT value FROM metadata WHERE key='playtime_backfill_v1'").fetchone()
    return {'coverage':json.loads(row['value']) if row else {}, 'players':[dict(r) for r in db.execute('''
      SELECT t.*,i.name FROM playtime_totals t JOIN identities i USING(creator_id) ORDER BY seconds DESC''')],
      'unresolved_sessions':int((db.execute("SELECT value FROM metadata WHERE key='playtime_unresolved'").fetchone() or ['0'])[0])}
