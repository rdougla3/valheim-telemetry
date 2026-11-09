"""Combat confirmed by the server's owned-character damage/death handlers.

Kept separate from combat.py (the existing Survival pipeline) and the retired
passive combat tables. Skills are per-character historical observations.
"""
import json
import math

SKILLS = {
    'Swords': 'Swords', 'Knives': 'Knives', 'Clubs': 'Clubs',
    'Polearms': 'Polearms', 'Spears': 'Spears', 'Axes': 'Axes',
    'Bows': 'Bows', 'Crossbows': 'Crossbows', 'Unarmed': 'Unarmed',
    'ElementalMagic': 'Elemental magic', 'BloodMagic': 'Blood magic',
    'Blocking': 'Blocking',
}
ADAPTER = 'server_owned_v1'


def initialize(db):
    db.executescript('''
      CREATE TABLE IF NOT EXISTS telemetry_combat_runs(epoch TEXT,boot TEXT,started REAL,
        PRIMARY KEY(epoch,boot));
      CREATE TABLE IF NOT EXISTS telemetry_combat_metrics(epoch TEXT,creator TEXT,
        kills INTEGER DEFAULT 0,participation INTEGER DEFAULT 0,PRIMARY KEY(epoch,creator));
      CREATE TABLE IF NOT EXISTS telemetry_combat_skills(epoch TEXT,creator TEXT,skill TEXT,
        max_level REAL,last_level REAL,last_seen REAL,PRIMARY KEY(epoch,creator,skill));
      CREATE TABLE IF NOT EXISTS telemetry_combat_enemies(epoch TEXT,creator TEXT,prefab TEXT,
        enemy TEXT,kills INTEGER DEFAULT 0,participation INTEGER DEFAULT 0,
        PRIMARY KEY(epoch,creator,prefab));
      CREATE TABLE IF NOT EXISTS telemetry_combat_deaths(epoch TEXT,subject TEXT,ts REAL,
        prefab TEXT,enemy TEXT,killer TEXT,participants TEXT,PRIMARY KEY(epoch,subject));
    ''')


def actor(value):
    return value if isinstance(value,str) and 0 < len(value) <= 80 else None


def start(db,epoch,boot,ts):
    db.execute('INSERT OR IGNORE INTO telemetry_combat_runs VALUES(?,?,?)',(epoch,boot,ts))


def reduce_event(db,e,epoch):
    p=e['data'];kind=e['kind'];ts=e['ts'];subject=p.get('subject')
    if p.get('source') != ADAPTER or not isinstance(subject,str) or not 0 < len(subject) <= 160:
        return
    if kind == 'combat_hit':
        creator=actor(p.get('creator'));damage=p.get('damage')
        if not creator or not isinstance(damage,(int,float)) or not math.isfinite(damage) or damage<=0:
            return
        start(db,epoch,e['boot'],ts)
        skill=p.get('skill');level=p.get('skill_level')
        if skill not in SKILLS or isinstance(level,bool) or not isinstance(level,(int,float)) or not math.isfinite(level) or not 0<=level<=100:
            return
        db.execute('''INSERT INTO telemetry_combat_skills VALUES(?,?,?,?,?,?)
          ON CONFLICT(epoch,creator,skill) DO UPDATE SET
          max_level=MAX(max_level,excluded.max_level),
          last_level=CASE WHEN excluded.last_seen>=last_seen THEN excluded.last_level ELSE last_level END,
          last_seen=MAX(last_seen,excluded.last_seen)''',(epoch,creator,skill,level,level,ts))
        db.execute('INSERT OR IGNORE INTO telemetry_combat_metrics(epoch,creator) VALUES(?,?)',(epoch,creator))
    elif kind == 'combat_death':
        prefab=p.get('prefab');enemy=p.get('enemy');raw=p.get('participants',[])
        if not isinstance(prefab,str) or not 0<len(prefab)<=160 or not isinstance(enemy,str) or not 0<len(enemy)<=160:
            raise ValueError('Invalid combat enemy')
        if not isinstance(raw,list) or len(raw)>64:
            raise ValueError('Invalid combat participants')
        killer=actor(p.get('killer'));participants={c for c in raw if actor(c)}
        if killer:participants.add(killer)
        if not db.execute('INSERT OR IGNORE INTO telemetry_combat_deaths VALUES(?,?,?,?,?,?,?)',
            (epoch,subject,ts,prefab,enemy,killer,json.dumps(sorted(participants)))).rowcount:
            return
        start(db,epoch,e['boot'],ts)
        for creator in participants:
            kill=int(creator==killer)
            db.execute('''INSERT INTO telemetry_combat_metrics VALUES(?,?,?,1)
              ON CONFLICT(epoch,creator) DO UPDATE SET kills=kills+excluded.kills,
              participation=participation+1''',(epoch,creator,kill))
            db.execute('''INSERT INTO telemetry_combat_enemies VALUES(?,?,?,?,?,1)
              ON CONFLICT(epoch,creator,prefab) DO UPDATE SET enemy=excluded.enemy,
              kills=kills+excluded.kills,participation=participation+1''',(epoch,creator,prefab,enemy,kill))


def summary(db,epoch,details):
    identities={r['creator_id']:r['name'] for r in db.execute('SELECT creator_id,name FROM identities')}
    metrics={r['creator']:dict(r) for r in db.execute('SELECT * FROM telemetry_combat_metrics WHERE epoch=?',(epoch,))}
    players=[]
    for creator in identities.keys() | metrics.keys():
        m=metrics.get(creator,{})
        skills=[dict(r) for r in db.execute('''SELECT skill,max_level AS level,last_level,last_seen
          FROM telemetry_combat_skills WHERE epoch=? AND creator=? ORDER BY max_level DESC,skill''',(epoch,creator))]
        for s in skills:
            s['name']=SKILLS.get(s['skill'],s['skill'])
            # Normal arrow impacts leave HitData.m_skillLevel unset (zero).
            # Preserve the raw evidence, but never present that default as a
            # measured Bows level or let it win a highest-skill comparison.
            if s['skill']=='Bows':
                if s['level']==0:s['level']=None
                if s['last_level']==0:s['last_level']=None
        skills.sort(key=lambda s:(s['level'] is None,-(s['level'] or 0),s['skill']))
        highest=next((s for s in skills if s['level'] is not None),None)
        players.append(dict(creator_id=creator,name=identities.get(creator) or 'Creator '+creator,
            kills=m.get('kills',0),participation=m.get('participation',0),
            highest_skill=highest,skills=skills,
            enemies=[dict(r) for r in db.execute('''SELECT prefab,enemy,kills,participation FROM telemetry_combat_enemies
              WHERE epoch=? AND creator=? ORDER BY kills DESC,participation DESC,enemy,prefab''',(epoch,creator))]))
    players.sort(key=lambda p:(-p['kills'],-p['participation'],p['name']))
    coverage=db.execute('SELECT MIN(started) FROM telemetry_combat_runs WHERE epoch=?',(epoch,)).fetchone()[0]
    deaths=db.execute('SELECT COUNT(*),COUNT(killer) FROM telemetry_combat_deaths WHERE epoch=?',(epoch,)).fetchone()
    return dict(started=coverage,active=details.get('combat_adapter')==ADAPTER,players=players,
        kills=sum(p['kills'] for p in players),participation=sum(p['participation'] for p in players),
        observed_deaths=deaths[0],unattributed_deaths=deaths[0]-deaths[1])
