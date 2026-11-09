import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import app
import playtime
import telemetry
from test_dashboard import fixture,pack
from world import read_world,stable_hash


class TelemetryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.patch=patch.object(app,'DB',self.root/'test.sqlite3');self.patch.start();app.initialize();self.db=app.connect()
        self.db.execute('DELETE FROM identities')
        for c,n in [('a','Alpha'),('b','Beta')]:self.db.execute('INSERT INTO identities VALUES(?,?,?,?,?,?)',(c,n,c,0,0,'test'))
        self.seq=0
    def tearDown(self):self.db.close();self.patch.stop();self.tmp.cleanup()
    def event(self,kind,data=None,ts=None,boot='boot',clock=1000):
        self.seq+=1
        return dict(schema=1,boot=boot,seq=self.seq,world='world',ts=ts if ts is not None else self.seq,clock=clock,kind=kind,data=data or {})
    def send(self,kind,data,**kwargs):
        e=self.event(kind,data,**kwargs);telemetry.accept(self.db,e);return e
    def metric(self,c='a'):
        return dict(self.db.execute('SELECT * FROM telemetry_metrics WHERE creator=?',(c,)).fetchone())
    def sample(self,x,mono,**extra):
        return self.send('sample',dict(creator='a',life='1:1',x=x,z=0,mono=mono,**extra))
    def test_distance_excludes_teleports_stale_gaps_respawns_and_boat_bobbing(self):
        self.sample(0,0);self.sample(5,1);self.sample(500,2);self.sample(501,3)
        self.sample(502,4,stale=True);self.sample(503,5);self.sample(504,6)
        self.sample(510,7,ship='boat',ship_x=100,ship_z=0)
        self.sample(520,8,ship='boat',ship_x=103,ship_z=0)
        self.assertAlmostEqual(self.metric()['foot'],7)
        self.assertAlmostEqual(self.metric()['boat'],3)
        self.send('sample',dict(creator='a',life='1:2',x=0,z=0,mono=9))
        self.assertAlmostEqual(self.metric()['foot'],7)
        self.assertEqual(self.metric()['farthest'],520)
    def test_slow_movement_is_accumulated(self):
        for i in range(11):self.sample(i/10,i)
        self.assertGreaterEqual(self.metric()['foot'],.9)
    def test_plant_credit_persists_after_replay_growth_removal_and_restart(self):
        data=dict(creator='a',subject='1:10',planted_at='12345',plant='Carrot',category='crop')
        e=self.send('plant',data);self.assertFalse(telemetry.accept(self.db,e))
        self.send('plant',data,boot='restart')
        self.assertEqual(self.metric()['planted'],1)
        self.send('plant',dict(data,subject='1:11',planted_at='12346'),boot='restart')
        self.assertEqual(self.metric()['planted'],2)
    def test_harvest_is_confirmed_once_and_ambiguous_actor_is_not_guessed(self):
        self.send('request',dict(subject='plant',creator='a',action='pick'),ts=10)
        self.send('harvest',dict(subject='plant',cycle=0,plant='Carrot',category='crop'),ts=11)
        self.send('harvest',dict(subject='plant',cycle=0,plant='Carrot',category='crop'),ts=12)
        self.assertEqual(self.metric()['harvested'],1)
        for c in ('a','b'):self.send('request',dict(subject='plant',creator=c,action='pick'),ts=20)
        self.send('harvest',dict(subject='plant',cycle=1,plant='Carrot',category='crop'),ts=21)
        self.assertEqual(self.metric()['harvested'],1)
        self.assertEqual(self.db.execute("SELECT count FROM telemetry_counts WHERE kind='agriculture.unattributed_harvested'").fetchone()[0],1)
    def test_retired_combat_records_are_ignored_without_stalling_replay(self):
        for kind in ('hit','kill_credit','mob_removed'):
            event=self.event(kind,dict(creator='a',subject='mob',skill='Swords',skill_level=35,enemy='Boar',health=0))
            self.assertFalse(telemetry.accept(self.db,event))
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telemetry_events').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telemetry_state').fetchone()[0],0)
        self.assertEqual(self.db.execute('SELECT seq FROM telemetry_runs').fetchone()[0],3)
        self.send('plant',dict(creator='a',subject='p',planted_at='1',plant='Turnip',category='crop'))
        self.assertEqual(self.metric()['planted'],1)
    def test_public_telemetry_excludes_retired_fields_even_from_old_schema(self):
        self.sample(0,0)
        for column in ('kills','participation'):
            self.db.execute('ALTER TABLE telemetry_metrics ADD COLUMN '+column+' INTEGER DEFAULT 99')
        telemetry.count(self.db,'boot','combat.old_counter',99)
        data=telemetry.summary(self.db,1)
        self.assertFalse({'kills','participation','skills','enemies'} & data['players'][0].keys())
        self.assertFalse(any(k.startswith('combat.') for k in data['counts']))
    def test_harvest_continues_after_observer_object_cache_expiration(self):
        for ts in (10,700):
            self.send('request',dict(subject='bush',creator='a',action='pick'),ts=ts)
            self.send('harvest',dict(subject='bush',cycle=0,plant='Raspberries',category='berry'),ts=ts+1)
        self.assertEqual(self.metric()['harvested'],2)
    def test_station_baseline_does_not_count_existing_contents(self):
        full=dict(subject='rack',type='cooking',slots=[dict(item='RawMeat',status=0)])
        self.send('station',full)
        self.assertIsNone(self.db.execute("SELECT count FROM telemetry_counts WHERE kind='station.inputs'").fetchone())
        self.send('station',dict(full,slots=[dict(item='CookedMeat',status=1)]))
        self.assertEqual(self.db.execute("SELECT count FROM telemetry_counts WHERE kind='station.cooked'").fetchone()[0],1)
    def test_spool_replay_and_partial_tail_cursor(self):
        f=self.root/'part.jsonl';first=self.event('plant',dict(creator='a',subject='p',planted_at='1',plant='Carrot',category='crop'))
        f.write_text(json.dumps(first)+'\n{"partial":')
        self.assertEqual(telemetry.ingest(self.db,self.root),1);self.assertEqual(telemetry.ingest(self.db,self.root),0)
        self.assertEqual(self.metric()['planted'],1)
        offset=self.db.execute('SELECT offset FROM telemetry_cursors').fetchone()[0]
        self.assertEqual(offset,len(json.dumps(first).encode())+1)
    def test_world_rollback_opens_new_epoch(self):
        self.sample(0,0);self.send('health',{},boot='restored',clock=10)
        self.assertEqual(self.db.execute('SELECT COUNT(DISTINCT epoch) FROM telemetry_runs').fetchone()[0],2)
    def test_invalid_record_is_quarantined_without_blocking_the_next_one(self):
        p=self.event('plant',dict(creator='a',subject='p',planted_at='1',plant='Carrot',category='crop'))
        (self.root/'part.jsonl').write_text('{"schema":99}\n'+json.dumps(p)+'\n')
        telemetry.ingest(self.db,self.root)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telemetry_quarantine').fetchone()[0],1)
        self.assertEqual(self.metric()['planted'],1)
    def test_playtime_merges_same_character_overlap_but_counts_death_time_online(self):
        for peer,a,b in [('1',0,100),('2',50,200),('3',300,400)]:
            self.db.execute('INSERT INTO sessions(platform_id,name,started,ended,peer) VALUES(?,?,?,?,?)',('a','Alpha',a,b,peer))
        playtime.refresh(self.db,500);r=playtime.summary(self.db)['players'][0]
        self.assertEqual(r['seconds'],300);self.assertEqual(r['sessions'],2)
    def test_playtime_backfill_fills_missing_start_once_and_ignores_respawn_none(self):
        messages=['Got handshake from client a','Got character ZDOID from Alpha : 1:1','Got character ZDOID from Alpha : 0:0','Got character ZDOID from Alpha : 1:5','Closing socket a']
        entries=[dict(__REALTIME_TIMESTAMP=str(i*1000000),MESSAGE=m) for i,m in enumerate(messages)]
        playtime.backfill(self.db,entries,10,lambda s:s);playtime.backfill(self.db,entries,10,lambda s:s)
        playtime.refresh(self.db,10)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM sessions').fetchone()[0],1)
        self.assertEqual(playtime.summary(self.db)['players'][0]['seconds'],3)
    def test_plants_are_excluded_from_surviving_pieces_and_map(self):
        wdir=self.root/'world';wdir.mkdir();fixture(wdir)
        f=wdir/'00_00__0_1.chunk';old=f.read_bytes()[6:]
        plant=pack('HfffI',0x20,11.,0.,20.,stable_hash('sapling_carrot'))+pack('BIq',1,stable_hash('creator'),101001)
        f.write_bytes(pack('hi',42,2)+old+plant)
        (wdir/'_main.1.chunks').write_bytes(pack('HiiHBIi',42,2,1,0,0,1,2))
        w=read_world(wdir);self.assertEqual(w['building_pieces'],1);self.assertEqual(w['excluded_plants'],1);self.assertEqual(len(w['map']['buildings']),1)


if __name__=='__main__':unittest.main()
