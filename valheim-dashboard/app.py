#!/usr/bin/env python3
"""Valheim dashboard: stdlib HTTP, SQLite, journal ingestion, read-only world metrics."""
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from datetime import datetime, timezone
import html
import config
import json
import logging
import mimetypes
import os
import re
import sqlite3
import subprocess
import threading
import time
from world import read_world, generation, plant_filter
import combat
import playtime
import telemetry

ROOT = Path(__file__).resolve().parent
DATA = config.DATA_DIR
WORLD = config.WORLD_PATH
LOCK = threading.RLock()
DB = DATA/'dashboard.sqlite3'


class Database(sqlite3.Connection):
    def __exit__(self, *args):
        try: return super().__exit__(*args)
        finally: self.close()


def connect():
    c = sqlite3.connect(DB, factory=Database); c.row_factory = sqlite3.Row
    c.execute('PRAGMA journal_mode=WAL')
    return c


def initialize():
    DATA.mkdir(parents=True, exist_ok=True)
    with connect() as db:
        db.executescript('''
        CREATE TABLE IF NOT EXISTS metadata(key TEXT PRIMARY KEY, value TEXT);
        CREATE TABLE IF NOT EXISTS events(cursor TEXT PRIMARY KEY, ts REAL, kind TEXT, payload TEXT);
        CREATE TABLE IF NOT EXISTS sessions(id INTEGER PRIMARY KEY, platform_id TEXT, name TEXT,
            started REAL, ended REAL, source TEXT, peer TEXT);
        CREATE TABLE IF NOT EXISTS identities(creator_id TEXT PRIMARY KEY, name TEXT, platform_id TEXT,
            first_seen REAL, last_seen REAL, source TEXT);
        CREATE TABLE IF NOT EXISTS snapshots(generation INTEGER PRIMARY KEY, captured REAL, payload TEXT);
        CREATE INDEX IF NOT EXISTS sessions_time ON sessions(started, ended);
        CREATE TABLE IF NOT EXISTS session_presence(session_id INTEGER PRIMARY KEY,
            last_seen REAL, missing_since REAL);
        ''')
        combat.initialize(db)
        playtime.initialize(db)
        telemetry.initialize(db)



def platform(value):
    match = re.search(r'765\d{14}', value or '')
    return match.group() if match else value or ''


def remember(db, creator, name, account, ts, source):
    if not creator or not name: return
    existing=db.execute('SELECT source FROM identities WHERE creator_id=?',(str(creator),)).fetchone()
    if source=='world creatorIndex' and existing and existing['source']!='world creatorIndex': return
    db.execute('''INSERT INTO identities VALUES(?,?,?,?,?,?) ON CONFLICT(creator_id) DO UPDATE SET
        name=excluded.name,platform_id=CASE WHEN excluded.platform_id!='' THEN excluded.platform_id ELSE identities.platform_id END,
        last_seen=excluded.last_seen,source=excluded.source''',
        (str(creator),name,platform(account),ts,ts,source))


def journal_command():
    """Read Docker logs and, optionally, retained native-service history."""
    container = os.environ.get('VALHEIM_JOURNAL_CONTAINER', '')
    unit = os.environ.get('VALHEIM_JOURNAL_USER_UNIT', 'valheim.service')
    if container:
        args = ['journalctl', '--no-pager', '-o', 'json']
        if unit:
            uid = os.environ.get('RUN_UID', str(os.getuid()))
            args += ['_SYSTEMD_USER_UNIT=' + unit, '_UID=' + uid, '+',
                     'USER_UNIT=' + unit, '_UID=' + uid, '+']
        return args + ['CONTAINER_NAME=' + container]
    return ['journalctl','--user','-u',unit or 'valheim.service','--no-pager','-o','json']


def journal_ingest():
    with connect() as db:
        row = db.execute("SELECT value FROM metadata WHERE key='journal_cursor'").fetchone()
        args = journal_command()
        if row: args += ['--after-cursor',row['value']]
        result = subprocess.run(args,capture_output=True,text=True,timeout=20)
        if result.returncode: raise RuntimeError(result.stderr.strip())
        pending = json.loads((db.execute("SELECT value FROM metadata WHERE key='pending_handshakes'").fetchone() or {'value':'[]'})['value'])
        last = None
        for line in result.stdout.splitlines():
            entry = json.loads(line); cursor=entry['__CURSOR']; last=cursor
            ts=int(entry['__REALTIME_TIMESTAMP'])/1e6; msg=entry.get('MESSAGE','')
            if not isinstance(msg,str): continue
            combat.capture_entry(db,entry)
            if db.execute('SELECT 1 FROM events WHERE cursor=?',(cursor,)).fetchone(): continue
            kind=None; payload={}
            if 'VALHEIM_IDENTITY ' in msg:
                try: payload=json.loads(msg.split('VALHEIM_IDENTITY ',1)[1])
                except (ValueError,IndexError): continue
                remember(db,payload.get('creator_id'),payload.get('name'),payload.get('platform_id'),ts,'live player ZDO')
                db.execute('UPDATE sessions SET platform_id=? WHERE peer=? AND ended IS NULL',
                           (platform(payload.get('platform_id')),str(payload.get('peer'))))
                kind='identity'
            elif match:=re.search(r'Got handshake from client (\S+)',msg):
                pending.append({'id':platform(match[1]),'ts':ts}); pending=pending[-20:]
            elif match:=re.search(r'Got character ZDOID from (.+?)\s*:\s*(-?\d+):\d+',msg):
                name,peer=match.groups()
                if int(peer)==0: continue  # ZDOID.None during death/respawn, not a login.
                candidates=[p for p in pending if 0<=ts-p['ts']<180]
                # Do not guess account identity when multiple logins overlap.
                account=candidates[0]['id'] if len(candidates)==1 else ''
                if len(candidates)==1: pending.remove(candidates[0])
                old=db.execute('SELECT id FROM sessions WHERE peer=? AND ended IS NULL',(peer,)).fetchone()
                if not old:
                    db.execute('INSERT INTO sessions(platform_id,name,started,source,peer) VALUES(?,?,?,?,?)',
                               (account,name,ts,'server journal',peer))
                    kind='join';payload={'name':name}
            elif match:=re.search(r'Closing socket (\S+)',msg):
                account=platform(match[1])
                sessions=db.execute('SELECT id,name FROM sessions WHERE platform_id=? AND ended IS NULL',(account,)).fetchall()
                for session in sessions:
                    db.execute('UPDATE sessions SET ended=? WHERE id=?',(ts,session['id']))
                if sessions:kind='leave';payload={'name':sessions[-1]['name']}
                pending=[p for p in pending if p['id']!=account]
            elif re.search(r'Stopping valheim.service|Stopped valheim.service|Main process exited|Starting valheim.service|Started valheim.service|VALHEIM_SERVER_START|Game - OnApplicationQuit',msg):
                db.execute('UPDATE sessions SET ended=? WHERE ended IS NULL',(ts,))
                pending=[]
            if kind:
                db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',(cursor,ts,kind,json.dumps(payload)))
        if last:
            db.execute("INSERT OR REPLACE INTO metadata VALUES('journal_cursor',?)",(last,))
            db.execute("INSERT OR REPLACE INTO metadata VALUES('pending_handshakes',?)",(json.dumps(pending),))


def live_players():
    try:
        doc=json.loads((DATA/'live.json').read_text())
        stamp=datetime.fromisoformat(doc['observed_at'].replace('Z','+00:00')).timestamp()
        if time.time()-stamp<25: return doc
    except (OSError,ValueError,KeyError): pass
    return None


def reconcile_presence(db, live):
    """Close missed disconnects only after repeated fresh absence, never on ZDO loss."""
    stamp=datetime.fromisoformat(live['observed_at'].replace('Z','+00:00')).timestamp()
    peers={str(p['peer']):p for p in live['players']}
    for session in db.execute('SELECT * FROM sessions WHERE ended IS NULL').fetchall():
        if session['started']>stamp: continue  # Heartbeat predates this login.
        sid=session['id']
        if str(session['peer']) in peers:
            player=peers[str(session['peer'])]
            db.execute('INSERT OR REPLACE INTO session_presence VALUES(?,?,NULL)',(sid,stamp))
            account=platform(player.get('platform_id'))
            if account: db.execute('UPDATE sessions SET platform_id=? WHERE id=?',(account,sid))
            continue
        presence=db.execute('SELECT * FROM session_presence WHERE session_id=?',(sid,)).fetchone()
        if not presence or presence['missing_since'] is None:
            db.execute('INSERT OR REPLACE INTO session_presence VALUES(?,?,?)',
                       (sid,presence['last_seen'] if presence else None,stamp))
        elif stamp-presence['missing_since']>=20:
            ended=max(session['started'],presence['last_seen'] or presence['missing_since'])
            db.execute('UPDATE sessions SET ended=? WHERE id=?',(ended,sid))
            db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?)',
                       (f"presence-close:{sid}",ended,'leave',json.dumps({'name':session['name'],'estimated':True})))


def collect():
    global collection_error
    with LOCK:
        journal_ingest()
        with connect() as db:
            if any(not db.execute('SELECT 1 FROM metadata WHERE key=?',(marker,)).fetchone() for marker in ('combat_backfill_v1','playtime_backfill_v1')):
                history=subprocess.run(journal_command(),capture_output=True,text=True,timeout=30,check=True)
                entries=[json.loads(line) for line in history.stdout.splitlines()]
                combat.backfill(db,entries,time.time())
                playtime.backfill(db,entries,time.time(),platform)
            live=live_players()
            if live:
                reconcile_presence(db,live)
                for p in live['players']:
                    remember(db,p.get('creator_id'),p.get('name'),p.get('platform_id'),time.time(),'live player ZDO')
            gen=generation(WORLD)
            previous=db.execute('SELECT payload FROM snapshots WHERE generation=?',(gen,)).fetchone()
            if not previous or json.loads(previous['payload']).get('parser_version')!=4 or json.loads(previous['payload']).get('plant_filter')!=plant_filter()[1]:
                doc=read_world(WORLD)
                for ident in doc.get('identities',[]):
                    remember(db,ident['creator_id'],ident['name'],ident['platform_id'],doc['saved_at'],'world creatorIndex')
                db.execute('INSERT OR REPLACE INTO snapshots VALUES(?,?,?)',(doc['generation'],time.time(),json.dumps(doc)))
            combat.refresh(db,live,time.time())
            playtime.refresh(db,time.time())
            try:
                telemetry.ingest(db,DATA/'telemetry')
                telemetry.maintenance(db,DATA/'telemetry',time.time())
                db.execute("DELETE FROM metadata WHERE key='telemetry_error'")
            except (OSError,ValueError,sqlite3.Error) as e:
                db.execute("INSERT OR REPLACE INTO metadata VALUES('telemetry_error',?)",(str(e),))
                logging.warning('Telemetry delayed: %s',e)
    collection_error=None


collection_error=None

def collector():
    global collection_error
    while True:
        try: collect()
        except Exception as e:
            collection_error=str(e);logging.exception('Collection failed; retaining last good snapshot')
        time.sleep(10)


def dashboard():
    now=time.time(); since=now-86400
    with LOCK,connect() as db:
        row=db.execute('SELECT payload FROM snapshots ORDER BY generation DESC LIMIT 1').fetchone()
        world=json.loads(row['payload']) if row else None
        identities={r['creator_id']:dict(r) for r in db.execute('SELECT * FROM identities')}
        sessions=[dict(r) for r in db.execute('SELECT * FROM sessions WHERE COALESCE(ended,?)>=? ORDER BY started DESC',(now,since))]
        recent=[dict(r) for r in db.execute("SELECT ts,kind,payload FROM events WHERE kind IN ('join','leave') ORDER BY ts DESC LIMIT 8")]
        first=db.execute('SELECT MIN(started) AS ts FROM sessions').fetchone()['ts']
        all_sessions=[dict(r) for r in db.execute('SELECT started,ended FROM sessions')]
        combat_summary=combat.summary(db)
        playtime_summary=playtime.summary(db)
        telemetry_summary=telemetry.summary(db,now)
    live=live_players()
    if live:
        online=[{'name':p['name'],'creator_id':p.get('creator_id')} for p in live['players']]
    else:
        active=(False if os.environ.get('VALHEIM_JOURNAL_CONTAINER') else
                subprocess.run(['systemctl','--user','is-active',os.environ.get('VALHEIM_JOURNAL_USER_UNIT') or 'valheim.service'],capture_output=True,text=True,timeout=3).stdout.strip()=='active')
        online=[{'name':s['name'],'creator_id':None} for s in sessions if s['ended'] is None] if active else []
    activity=[]
    for i in range(24):
        start=since+i*3600;end=start+3600
        seconds=sum(max(0,min(s['ended'] or now,end)-max(s['started'],start)) for s in sessions)
        activity.append({'ts':start,'player_minutes':round(seconds/60,1)})
    changes=[]
    for s in all_sessions:
        changes.append((s['started'],1))
        if s['ended'] is not None:changes.append((s['ended'],-1))
    current=peak=0
    for _,delta in sorted(changes,key=lambda x:(x[0],x[1])):
        current+=delta;peak=max(peak,current)
    for event in recent:event['name']=json.loads(event.pop('payload')).get('name','Unknown')
    if world:
        if live and live.get('day',-1)>=0:
            world['day']=live['day']; world['day_source']='live world clock'
        else: world['day_source']='saved clock, standard 30-minute cycle'
        for r in world['leaderboard']:
            ident=identities.get(r['creator_id'])
            r['name']=ident['name'] if ident else None
            r['identity_source']=ident['source'] if ident else None
    player_hours=round(sum(max(0,(s['ended'] or now)-max(s['started'],since)) for s in sessions)/3600,2)
    crossplay=None
    configured_crossplay=os.environ.get('CROSSPLAY')
    if configured_crossplay is not None:
        crossplay=configured_crossplay.lower()=='true'
    else:
        try:
            pid=subprocess.check_output(['systemctl','--user','show',os.environ.get('VALHEIM_JOURNAL_USER_UNIT') or 'valheim.service','-p','MainPID','--value'],text=True,timeout=3).strip()
            crossplay=b'-crossplay' in Path(f'/proc/{pid}/cmdline').read_bytes().split(b'\0')
        except (OSError,subprocess.SubprocessError): pass
    return {'updated_at':now,'error':collection_error,'world':world,'crossplay':crossplay,'survival':combat_summary,
            'playtime':playtime_summary,'telemetry':telemetry_summary,
            'activity':{'online':online,'online_count':len(online),'peak':peak,
            'unique_24h':len({s['platform_id'] or s['name'] for s in sessions}),
            'player_hours_24h':player_hours,
            # Compatibility for tabs running the short-lived occupied-hours frontend.
            'occupied_hours_24h':player_hours,
            'first_recorded':first,'hourly':activity,'recent':recent,
            'sessions':[{'name':s['name'],'started':s['started'],'ended':s['ended']} for s in sessions[:20]]},
            'identity_observer':bool(live)}


def render_index(body):
    # Deliberate whitelist: server credentials never enter frontend configuration.
    for key, value in config.PUBLIC_DISPLAY.items():
        body=body.replace(('{{'+key+'}}').encode(),html.escape(value,quote=True).encode())
    return body


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        route=self.path.split('?',1)[0]
        for prefix in config.URL_PREFIXES:
            if route == prefix:
                self.send_response(308)
                self.send_header('Location', prefix + '/')
                self.send_header('Content-Length', '0')
                self.end_headers()
                return
            if route.startswith(prefix + '/'):
                route = route[len(prefix):]
                break
        if route=='/api/dashboard':
            try: body=json.dumps(dashboard()).encode();code=200
            except Exception:
                logging.exception('API error');body=b'{"error":"Metrics temporarily unavailable"}';code=503
            self.send_response(code);self.send_header('Content-Type','application/json');self.send_header('Cache-Control','no-store')
        else:
            path=ROOT/'static'/('index.html' if route=='/' else route.lstrip('/'))
            if not path.resolve().is_relative_to((ROOT/'static').resolve()) or not path.is_file():
                self.send_error(404);return
            body=path.read_bytes()
            if path.name=='index.html':
                body=render_index(body)
                for asset,attribute in [('app.js','src'),('style.css','href'),('fonts.css','href')]:
                    version=(ROOT/'static'/asset).stat().st_mtime_ns
                    body=body.replace(f'{attribute}="{asset}"'.encode(),f'{attribute}="{asset}?v={version}"'.encode())
            self.send_response(200)
            self.send_header('Content-Type',mimetypes.guess_type(path)[0] or 'application/octet-stream')
            self.send_header('Cache-Control','no-cache')
        self.send_header('Content-Length',str(len(body)))
        self.send_header('Cloudflare-CDN-Cache-Control','no-store')
        self.send_header('X-Content-Type-Options','nosniff')
        self.send_header('Content-Security-Policy',"default-src 'self'; style-src 'self'; script-src 'self'; font-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers();self.wfile.write(body)
    def log_message(self, fmt, *args):
        if args and 'api/dashboard' in str(args[0]):return
        logging.info(fmt,*args)


if __name__=='__main__':
    logging.basicConfig(level=logging.INFO,format='%(asctime)s %(levelname)s %(message)s')
    initialize()
    try: collect()
    except Exception as e: collection_error=str(e);logging.exception('Initial collection failed')
    threading.Thread(target=collector,daemon=True).start()
    host=os.environ.get('DASHBOARD_HOST','0.0.0.0');port=int(os.environ.get('DASHBOARD_PORT','8080'))
    logging.info('Valheim dashboard listening on http://%s:%s',host,port)
    ThreadingHTTPServer((host,port),Handler).serve_forever()
