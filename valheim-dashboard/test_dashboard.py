import gzip
import json
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch
import app
from world import read_world, stable_hash


def pack(fmt,*values):return struct.pack('<'+fmt,*values)
def string(s):
    b=s.encode();n=len(b);out=bytearray()
    while n>=128:out.append((n&127)|128);n>>=7
    out.append(n);return bytes(out)+b


def fixture(path):
    zone=pack('i',2)+pack('hhhh',0,0,15625,15625)+pack('ii',32,1)+string('defeated_eikthyr')+pack('Bi',1,0)
    compressed=gzip.compress(zone)
    (path/'_main.1.db2').write_bytes(pack('idi',42,3600.,len(compressed))+compressed)
    meta=pack('i',42)+string('Test')+string('seed')+pack('iqiBii',0,123,2,1,0,1)
    meta+=string('Steam_76500000000000001')+string('Test Viking')+string('Test Viking')+string('')
    (path/'_main.1.fwl2').write_bytes(pack('i',len(meta))+meta)
    (path/'_main.1.chunks').write_bytes(pack('HiiHBIi',42,1,1,0,0,1,1))
    obj=pack('HfffI',0x30,10.,0.,20.,stable_hash('stone_pile'))
    obj+=pack('BIi',1,stable_hash('creatorIndex'),0)+pack('BIq',1,stable_hash('creator'),101001)
    (path/'00_00__0_1.chunk').write_bytes(pack('hi',42,1)+obj)
    (path/'_main.1.ok').write_bytes(pack('i',1))


class ParserTests(unittest.TestCase):
    def test_creator_identity_and_offworld_filter(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);fixture(path);w=read_world(path)
            self.assertEqual(w['leaderboard'],[{'creator_id':'101001','pieces':1}])
            self.assertEqual(w['identities'][0]['name'],'Test Viking')
            self.assertEqual(w['day'],2)
            self.assertEqual(w['generated_zones'],1)
            self.assertEqual(w['off_map_zones'],1)
            self.assertAlmostEqual(w['generated_km2'],.004096)
            self.assertTrue(w['bosses'][0]['defeated'])
    def test_portal_pairing_from_dedicated_portal_chunk(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);fixture(path)
            (path/'_main.1.chunks').write_bytes(pack('Hii',42,3,2)+pack('HBIi',0,0,1,1)+pack('HBIi',1,0,1,2))
            records=b''
            for x in (100.,200.):
                records+=pack('HfffI',0x40,x,0.,0.,stable_hash('portal_wood'))
                records+=pack('BI',1,stable_hash('tag'))+string('Home')
            (path/'00_01__0_1.chunk').write_bytes(pack('hi',42,2)+records)
            w=read_world(path)
            self.assertEqual(len(w['portals']),2)
            self.assertEqual(len(w['portal_pairs']),1)
            self.assertEqual(w['portal_pairs'][0]['tag'],'Home')
            self.assertEqual(w['unpaired_portals'],0)
    def test_reject_partial_save_and_chunk_corruption(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp);fixture(path)
            f=path/'00_00__0_1.chunk';f.write_bytes(f.read_bytes()[:-1])
            with self.assertRaises((ValueError,struct.error)):read_world(path)
            (path/'_main.1.ok').unlink()
            with self.assertRaisesRegex(ValueError,'No committed'):read_world(path)


class JournalTests(unittest.TestCase):
    def test_overlapping_logins_do_not_guess_accounts_and_disconnects_close_sessions(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',Path(tmp)/'test.sqlite3'):
            app.initialize()
            messages=['Started valheim.service - server',
                      'Got handshake from client 76500000000000001',
                      'Got handshake from client 76500000000000002',
                      'Got character ZDOID from First : -111:1',
                      'Got character ZDOID from Second : -222:1',
                      'VALHEIM_IDENTITY '+json.dumps({'creator_id':'11','peer':'-111','platform_id':'76500000000000001','name':'First'}),
                      'VALHEIM_IDENTITY '+json.dumps({'creator_id':'22','peer':'-222','platform_id':'76500000000000002','name':'Second'}),
                      'Closing socket 76500000000000001',
                      'Closing socket 76500000000000002']
            entries=[json.dumps({'__CURSOR':str(i),'__REALTIME_TIMESTAMP':str((1000+i)*1000000),'MESSAGE':m}) for i,m in enumerate(messages)]
            result=type('Result',(),{'returncode':0,'stdout':'\n'.join(entries),'stderr':''})()
            with patch.object(app.subprocess,'run',return_value=result):app.journal_ingest()
            with app.connect() as db:
                sessions=db.execute('SELECT * FROM sessions').fetchall()
                self.assertEqual(len(sessions),2)
                self.assertEqual({s['platform_id'] for s in sessions},{'76500000000000001','76500000000000002'})
                self.assertTrue(all(s['ended'] is not None for s in sessions))
                self.assertEqual(db.execute("SELECT value FROM metadata WHERE key='journal_cursor'").fetchone()[0],'8')
    def test_respawn_none_and_new_character_objects_do_not_create_sessions(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',Path(tmp)/'test.sqlite3'):
            app.initialize()
            messages=['Got handshake from client 76500000000000003',
                      'Got character ZDOID from Test Player : 3260928961:1',
                      'Destroying abandoned non persistent zdo 3260928961:1 owner 3260928961',
                      'Got character ZDOID from Test Player : 0:0',
                      'Got character ZDOID from Test Player : 3260928961:8369',
                      'Got character ZDOID from Test Player : 0:0',
                      'Got character ZDOID from Test Player : 3260928961:10074',
                      'Closing socket 76500000000000003']
            entries=[json.dumps({'__CURSOR':str(i),'__REALTIME_TIMESTAMP':str((1000+i)*1000000),'MESSAGE':m}) for i,m in enumerate(messages)]
            result=type('Result',(),{'returncode':0,'stdout':'\n'.join(entries),'stderr':''})()
            with patch.object(app.subprocess,'run',return_value=result):app.journal_ingest()
            with app.connect() as db:
                rows=db.execute('SELECT * FROM sessions').fetchall()
                self.assertEqual(len(rows),1)
                self.assertEqual(rows[0]['ended'],1007)
                self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='join'").fetchone()[0],1)
    def test_presence_closes_missed_disconnect_after_grace_and_recovers_from_transient_absence(self):
        def heartbeat(stamp,present):
            return {'observed_at':app.datetime.fromtimestamp(stamp,app.timezone.utc).isoformat(),
                    'players':[{'peer':'123','name':'Test Player','platform_id':'76500000000000003'}] if present else []}
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',Path(tmp)/'test.sqlite3'):
            app.initialize()
            with app.connect() as db:
                db.execute("INSERT INTO sessions(platform_id,name,started,peer) VALUES('','Test Player',90,'123')")
                app.reconcile_presence(db,heartbeat(100,True))
                app.reconcile_presence(db,heartbeat(110,False))
                app.reconcile_presence(db,heartbeat(120,True))
                app.reconcile_presence(db,heartbeat(130,False))
                app.reconcile_presence(db,heartbeat(140,False))
                self.assertIsNone(db.execute('SELECT ended FROM sessions').fetchone()[0])
                app.reconcile_presence(db,heartbeat(150,False))
                self.assertEqual(db.execute('SELECT ended FROM sessions').fetchone()[0],120)
    def test_live_identity_overrides_account_display_name(self):
        with tempfile.TemporaryDirectory() as tmp,patch.object(app,'DB',Path(tmp)/'test.sqlite3'):
            app.initialize()
            with app.connect() as db:
                app.remember(db,'123','Account','Steam_76500000000000001',1,'world creatorIndex')
                app.remember(db,'123','Character','76500000000000001',2,'live player ZDO')
                app.remember(db,'123','Account','Steam_76500000000000001',3,'world creatorIndex')
                self.assertEqual(db.execute("SELECT name FROM identities WHERE creator_id='123'").fetchone()[0],'Character')


if __name__=='__main__':unittest.main()
