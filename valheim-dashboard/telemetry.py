"""Incremental passive telemetry ingestion. SQLite commits evidence and cursor together."""
from pathlib import Path
import json
import math
import combat_telemetry


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS telemetry_runs(boot TEXT PRIMARY KEY, world TEXT, epoch TEXT,
        started REAL, last_seen REAL, world_seconds REAL, seq INTEGER, details TEXT);
      CREATE TABLE IF NOT EXISTS telemetry_events(boot TEXT,seq INTEGER,ts REAL,kind TEXT,
        creator TEXT,subject TEXT,payload TEXT,PRIMARY KEY(boot,seq));
      CREATE INDEX IF NOT EXISTS telemetry_event_time ON telemetry_events(ts);
      CREATE TABLE IF NOT EXISTS telemetry_cursors(file TEXT PRIMARY KEY,offset INTEGER NOT NULL);
      CREATE TABLE IF NOT EXISTS telemetry_quarantine(file TEXT,offset INTEGER,reason TEXT,payload TEXT,
        PRIMARY KEY(file,offset));
      CREATE TABLE IF NOT EXISTS telemetry_metrics(epoch TEXT,creator TEXT,foot REAL DEFAULT 0,
        boat REAL DEFAULT 0,other REAL DEFAULT 0,tracked_seconds REAL DEFAULT 0,farthest REAL DEFAULT 0,
        farthest_x REAL,farthest_z REAL,planted INTEGER DEFAULT 0,harvested INTEGER DEFAULT 0,
        PRIMARY KEY(epoch,creator));
      CREATE TABLE IF NOT EXISTS telemetry_crops(epoch TEXT,creator TEXT,plant TEXT,category TEXT,
        planted INTEGER DEFAULT 0,harvested INTEGER DEFAULT 0,PRIMARY KEY(epoch,creator,plant,category));
      CREATE TABLE IF NOT EXISTS telemetry_cells(epoch TEXT,creator TEXT,x INTEGER,z INTEGER,first REAL,last REAL,
        PRIMARY KEY(epoch,creator,x,z));
      CREATE TABLE IF NOT EXISTS telemetry_state(boot TEXT,subject TEXT,kind TEXT,ts REAL,payload TEXT,
        PRIMARY KEY(boot,subject,kind));
      CREATE TABLE IF NOT EXISTS telemetry_counts(epoch TEXT,kind TEXT,count INTEGER DEFAULT 0,PRIMARY KEY(epoch,kind));
      CREATE TABLE IF NOT EXISTS telemetry_confirmed(boot TEXT,subject TEXT,kind TEXT,cycle TEXT,
        PRIMARY KEY(boot,subject,kind,cycle));
      CREATE TABLE IF NOT EXISTS telemetry_plants(epoch TEXT,subject TEXT,planted_at TEXT,creator TEXT,
        PRIMARY KEY(epoch,subject,planted_at));
    ''')
    combat_telemetry.initialize(db)


def finite(*values):
    return all(isinstance(v,(int,float)) and math.isfinite(v) for v in values)


def count(db, epoch, kind, amount=1):
    db.execute('INSERT INTO telemetry_counts VALUES(?,?,?) ON CONFLICT(epoch,kind) DO UPDATE SET count=count+excluded.count', (epoch,kind,amount))


def metric(db, epoch, creator, field, amount):
    if not creator or field not in ('foot','boat','other','tracked_seconds','planted','harvested'): return
    db.execute('INSERT OR IGNORE INTO telemetry_metrics(epoch,creator) VALUES(?,?)',(epoch,creator))
    db.execute(f'UPDATE telemetry_metrics SET {field}={field}+? WHERE epoch=? AND creator=?',(amount,epoch,creator))


def state(db, boot, subject, kind):
    r=db.execute('SELECT ts,payload FROM telemetry_state WHERE boot=? AND subject=? AND kind=?',(boot,subject,kind)).fetchone()
    return (r['ts'],json.loads(r['payload'])) if r else (None,None)


def put_state(db, boot, subject, kind, ts, payload):
    db.execute('INSERT OR REPLACE INTO telemetry_state VALUES(?,?,?,?,?)',(boot,subject,kind,ts,json.dumps(payload,separators=(',',':'))))


def once(db, boot, subject, kind, cycle):
    return db.execute('INSERT OR IGNORE INTO telemetry_confirmed VALUES(?,?,?,?)',(boot,subject,kind,str(cycle))).rowcount == 1


def crop(db,epoch,creator,plant,category,field):
    if category=='gathering':
        count(db,epoch,'gathering.'+field);return
    count(db,epoch,'agriculture.'+field)
    if not creator:
        count(db,epoch,'agriculture.unattributed_'+field); return
    metric(db,epoch,creator,field,1)
    db.execute('INSERT OR IGNORE INTO telemetry_crops(epoch,creator,plant,category) VALUES(?,?,?,?)',(epoch,creator,plant,category))
    db.execute(f'UPDATE telemetry_crops SET {field}={field}+1 WHERE epoch=? AND creator=? AND plant=? AND category=?',(epoch,creator,plant,category))


def _request_actor(db,boot,subject,ts,actions,item=None):
    _,pending=state(db,boot,subject,'requests'); pending=pending or []
    possible=[r for r in pending if 0 <= ts-r['ts'] <= 5 and r['action'] in actions and (not item or r.get('item') in (item,None,''))]
    if len(possible)!=1: return None
    selected=possible[0]
    put_state(db,boot,subject,'requests',ts,[r for r in pending if r is not selected and ts-r['ts']<=5])
    return selected.get('creator')


def reduce_event(db,e,epoch):
    boot,ts,kind=e['boot'],e['ts'],e['kind'];p=e.get('data',{});creator=p.get('creator');subject=str(p.get('subject',''))
    if kind in ('combat_hit','combat_death'):
        combat_telemetry.reduce_event(db,e,epoch)
    elif kind=='sample' and creator:
        x,z=p.get('x'),p.get('z');mono=p.get('mono')
        if not finite(x,z,mono) or math.hypot(x,z)>11000: return
        if p.get('dead') or p.get('stale') or not p.get('life'):
            db.execute("DELETE FROM telemetry_state WHERE boot=? AND subject=? AND kind='position'",(boot,creator));return
        db.execute('INSERT OR IGNORE INTO telemetry_metrics(epoch,creator) VALUES(?,?)',(epoch,creator))
        radius=math.hypot(x,z)
        db.execute('UPDATE telemetry_metrics SET farthest=?,farthest_x=?,farthest_z=? WHERE epoch=? AND creator=? AND farthest<?',(radius,x,z,epoch,creator,radius))
        cell=(math.floor(x/64),math.floor(z/64))
        db.execute('INSERT INTO telemetry_cells VALUES(?,?,?,?,?,?) ON CONFLICT(epoch,creator,x,z) DO UPDATE SET last=excluded.last',(epoch,creator,*cell,ts,ts))
        _,prev=state(db,boot,creator,'position')
        base=dict(p)
        if prev and prev.get('life')==p.get('life'):
            dt=mono-prev['mono'];ship=p.get('ship');same_ship=ship and ship==prev.get('ship')
            distance=math.hypot(x-prev['x'],z-prev['z']);mode='foot'
            if same_ship and all(finite(p.get(k),prev.get(k)) for k in ('ship_x','ship_z')):
                distance=math.hypot(p['ship_x']-prev['ship_x'],p['ship_z']-prev['ship_z']);mode='boat'
            elif p.get('ship') or prev.get('ship'): mode='other'
            if p.get('mounted') and not same_ship: mode='other'
            # No bridge over stale replication, teleport flags, implausible speed, or missing samples.
            cap=35 if mode=='boat' else 18
            valid=0<dt<=3 and not p.get('teleport') and not prev.get('teleport') and distance<=cap*dt+2
            if valid:
                metric(db,epoch,creator,'tracked_seconds',dt)
                # Accumulate short movements instead of discarding every slow step.
                residual=(prev.get('residual',0) if prev.get('mode',mode)==mode else 0)+distance
                if residual>=.5:
                    metric(db,epoch,creator,mode,residual);residual=0
                base['residual']=residual if prev.get('mode',mode)==mode else 0
                base['mode']=mode
            else: count(db,epoch,'movement.gaps')
        put_state(db,boot,creator,'position',ts,base)
    elif kind=='plant':
        if not creator or not subject or not p.get('planted_at'): return
        if db.execute('INSERT OR IGNORE INTO telemetry_plants VALUES(?,?,?,?)',(epoch,subject,str(p['planted_at']),creator)).rowcount:
            crop(db,epoch,creator,p.get('plant','Unknown'),p.get('category','crop'),'planted')
    elif kind=='request' and subject:
        _,pending=state(db,boot,subject,'requests');pending=[r for r in (pending or []) if ts-r['ts']<=5]
        pending.append(dict(ts=ts,creator=creator,action=p.get('action'),item=p.get('item')))
        put_state(db,boot,subject,'requests',ts,pending[-16:])
    elif kind=='harvest' and subject:
        cycle=p.get('cycle',str(e['seq']))
        last_harvest,_=state(db,boot,subject,'harvest_confirmation')
        # Observer 2.0's bounded object cache expires after ten minutes. A repeated
        # counter after that gap belongs to a fresh cache generation, not the first
        # harvest of this object forever. Envelope keys still prevent spool replay.
        if last_harvest is not None and ts-last_harvest>600:
            db.execute("DELETE FROM telemetry_confirmed WHERE boot=? AND subject=? AND kind='harvest'",(boot,subject))
        if once(db,boot,subject,'harvest',cycle):
            actor=_request_actor(db,boot,subject,ts,('pick',))
            crop(db,epoch,actor,p.get('plant','Unknown'),p.get('category','wild'),'harvested')
            put_state(db,boot,subject,'harvest_confirmation',ts,{'cycle':cycle})
    elif kind=='station' and subject:
        _,old=state(db,boot,subject,'station')
        if old:
            typ=p.get('type')
            if typ=='cooking':
                for index,(a,b) in enumerate(zip(old.get('slots',[]),p.get('slots',[]))):
                    if a==b:continue
                    if not a.get('item') and b.get('item'):
                        count(db,epoch,'station.inputs');actor=_request_actor(db,boot,subject,ts,('add',),b['item'])
                        if not actor:count(db,epoch,'station.unattributed_inputs')
                    elif a.get('item') and b.get('item') and a.get('status')!=b.get('status') and b.get('status') in (1,2):
                        count(db,epoch,'station.burnt' if b.get('status')==2 else 'station.cooked')
                    elif a.get('item') and not b.get('item'):count(db,epoch,'station.removed_slots')
            elif typ=='smelter':
                if p.get('queue',0)>old.get('queue',0):count(db,epoch,'station.queued_inputs',p['queue']-old.get('queue',0))
                if p.get('queue',0)<old.get('queue',0):count(db,epoch,'station.processed_inputs_observed',old['queue']-p.get('queue',0))
            elif typ=='fermenter':
                if not old.get('content') and p.get('content'):count(db,epoch,'station.fermentation_started')
                elif old.get('content') and not p.get('content'):count(db,epoch,'station.fermentation_tapped')
        put_state(db,boot,subject,'station',ts,p)


def accept(db,e):
    if e.get('schema')!=1 or not isinstance(e.get('data',{}),dict):raise ValueError('Unsupported telemetry event')
    boot=str(e['boot']);seq=e['seq'];ts=e['ts'];world=str(e.get('world','unknown'));kind=e['kind']
    if not isinstance(seq,int) or seq<1 or not finite(ts) or len(boot)>80:raise ValueError('Invalid telemetry envelope')
    run=db.execute('SELECT * FROM telemetry_runs WHERE boot=?',(boot,)).fetchone()
    if run:
        epoch=run['epoch']
        if seq>run['seq']+1:count(db,epoch,'capture.sequence_gaps',seq-run['seq']-1)
    else:
        prev=db.execute('SELECT * FROM telemetry_runs WHERE world=? ORDER BY started DESC LIMIT 1',(world,)).fetchone()
        rollback=prev and finite(e.get('clock'),prev['world_seconds']) and e['clock']<prev['world_seconds']-60
        epoch=boot if not prev or rollback else prev['epoch']
        db.execute('INSERT INTO telemetry_runs VALUES(?,?,?,?,?,?,?,?)',(boot,world,epoch,ts,ts,e.get('clock'),0,'{}'))
        if rollback:count(db,epoch,'capture.world_rollback')
    # Consume retired observer records without retaining or reducing combat data.
    if kind in ('hit','kill_credit','mob_removed'):
        db.execute('UPDATE telemetry_runs SET seq=MAX(seq,?) WHERE boot=?',(seq,boot))
        return False
    if not db.execute('INSERT OR IGNORE INTO telemetry_events VALUES(?,?,?,?,?,?,?)',
         (boot,seq,ts,kind,e['data'].get('creator'),str(e['data'].get('subject','')),json.dumps(e,separators=(',',':')))).rowcount:return False
    if kind=='boot':
        db.execute('UPDATE telemetry_runs SET details=? WHERE boot=?',(json.dumps(e['data']),boot))
        if e['data'].get('combat_adapter')==combat_telemetry.ADAPTER:
            combat_telemetry.start(db,epoch,boot,ts)
    if kind=='health' and e['data'].get('dropped',0):
        # Health reports are cumulative, not quantities to sum every minute.
        old=db.execute('SELECT count FROM telemetry_counts WHERE epoch=? AND kind=?',(epoch,'capture.dropped:'+boot)).fetchone()
        if not old: db.execute('INSERT INTO telemetry_counts VALUES(?,?,?)',(epoch,'capture.dropped:'+boot,e['data']['dropped']))
        else:db.execute('UPDATE telemetry_counts SET count=MAX(count,?) WHERE epoch=? AND kind=?',(e['data']['dropped'],epoch,'capture.dropped:'+boot))
    reduce_event(db,e,epoch)
    db.execute('UPDATE telemetry_runs SET last_seen=MAX(last_seen,?),world_seconds=?,seq=MAX(seq,?) WHERE boot=?',(ts,e.get('clock'),seq,boot))
    return True


def ingest(db,path,limit=5000):
    path=Path(path);processed=0
    if not path.exists():return 0
    for f in sorted(path.glob('*.jsonl')):
        if f.is_symlink():continue
        row=db.execute('SELECT offset FROM telemetry_cursors WHERE file=?',(f.name,)).fetchone();offset=row['offset'] if row else 0
        with f.open('rb') as stream:
            if f.stat().st_size<offset:raise ValueError('Telemetry segment truncated after ingestion')
            stream.seek(offset)
            while processed<limit:
                start=stream.tell();line=stream.readline(262145)
                if not line:break
                if len(line)>262144:raise ValueError('Oversize telemetry line')
                if not line.endswith(b'\n'):stream.seek(start);break
                db.execute('SAVEPOINT telemetry_record')
                try:accept(db,json.loads(line))
                except (ValueError,KeyError,TypeError,OverflowError) as error:
                    db.execute('ROLLBACK TO telemetry_record')
                    db.execute('INSERT OR IGNORE INTO telemetry_quarantine VALUES(?,?,?,?)',(f.name,start,str(error)[:500],line.decode('utf-8',errors='replace')))
                finally:db.execute('RELEASE telemetry_record')
                processed+=1;offset=stream.tell()
        db.execute('INSERT OR REPLACE INTO telemetry_cursors VALUES(?,?)',(f.name,offset))
        if processed>=limit:break
    return processed


def maintenance(db,path,now):
    row=db.execute("SELECT value FROM metadata WHERE key='telemetry_maintenance'").fetchone()
    if row and now-float(row[0])<3600:return
    # Reducer state and cursors survive raw evidence retention; no historical replay from aggregates.
    db.execute("DELETE FROM telemetry_events WHERE ts<? AND kind IN ('sample','hit','combat_hit')",(now-7*86400,))
    db.execute('DELETE FROM telemetry_events WHERE ts<?',(now-30*86400,))
    db.execute("DELETE FROM telemetry_state WHERE ts<? AND kind IN ('hit','requests','position')",(now-3600,))
    for row in db.execute('SELECT file,offset FROM telemetry_cursors').fetchall():
        f=Path(path)/row['file']
        if f.exists() and f.stat().st_mtime<now-86400 and f.stat().st_size==row['offset']:
            f.unlink()  # Acknowledged, inactive spool only. Ledger retention is separate.
    db.execute("INSERT OR REPLACE INTO metadata VALUES('telemetry_maintenance',?)",(str(now),))


def summary(db,now):
    run=db.execute('SELECT * FROM telemetry_runs ORDER BY started DESC LIMIT 1').fetchone()
    if not run:return {'started':None,'healthy':False,'players':[],'counts':{},'coverage_note':'New telemetry has not started yet.'}
    epoch=run['epoch'];players=[]
    for identity in db.execute('SELECT * FROM identities').fetchall():
        creator=identity['creator_id'];r=db.execute('SELECT epoch,creator,foot,boat,other,tracked_seconds,farthest,farthest_x,farthest_z,planted,harvested FROM telemetry_metrics WHERE epoch=? AND creator=?',(epoch,creator)).fetchone()
        p=dict(r) if r else dict(creator=creator,foot=0,boat=0,other=0,tracked_seconds=0,farthest=0,planted=0,harvested=0)
        p.update(name=identity['name'],creator_id=creator)
        p['plants']=[dict(x) for x in db.execute('SELECT plant,category,planted,harvested FROM telemetry_crops WHERE epoch=? AND creator=? ORDER BY planted DESC,harvested DESC',(epoch,creator))]
        p['visited_cells']=db.execute('SELECT COUNT(*) FROM telemetry_cells WHERE epoch=? AND creator=?',(epoch,creator)).fetchone()[0]
        players.append(p)
    started=db.execute('SELECT MIN(started) FROM telemetry_runs WHERE epoch=?',(epoch,)).fetchone()[0]
    counts={r['kind']:r['count'] for r in db.execute("SELECT * FROM telemetry_counts WHERE epoch=? AND kind NOT LIKE 'combat.%'",(epoch,))}
    health=db.execute("SELECT payload FROM telemetry_events WHERE boot=? AND kind='health' ORDER BY seq DESC LIMIT 1",(run['boot'],)).fetchone()
    visited=db.execute('SELECT COUNT(*) FROM (SELECT DISTINCT x,z FROM telemetry_cells WHERE epoch=?)',(epoch,)).fetchone()[0]
    quarantined=db.execute('SELECT COUNT(*) FROM telemetry_quarantine').fetchone()[0]
    error=db.execute("SELECT value FROM metadata WHERE key='telemetry_error'").fetchone()
    return dict(started=started,last_seen=run['last_seen'],healthy=now-run['last_seen']<90 and not error,players=players,counts=counts,visited_cells=visited,quarantined=quarantined,error=error[0] if error else None,
                capabilities=json.loads(run['details']),health=json.loads(health[0])['data'] if health else {},
                combat=combat_telemetry.summary(db,epoch,json.loads(run['details'])),
                coverage_note='Observed since collection began. Owner-local harvest actions can be missing or unattributed. Distance is sampled; teleports and gaps are excluded.')
