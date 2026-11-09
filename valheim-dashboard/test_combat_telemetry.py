import json
import unittest
import combat_telemetry
import telemetry
import test_telemetry as fixtures


class ConfirmedCombatTests(unittest.TestCase):
    # Reuse the isolated SQLite/event fixture; tests never write live data.
    setUp=fixtures.TelemetryTests.setUp
    tearDown=fixtures.TelemetryTests.tearDown
    event=fixtures.TelemetryTests.event
    send=fixtures.TelemetryTests.send
    metric=fixtures.TelemetryTests.metric
    def hit(self,creator='a',skill='Polearms',level=41,subject='mob',**extra):
        return self.send('combat_hit',dict(source=combat_telemetry.ADAPTER,creator=creator,
            skill=skill,skill_level=level,subject=subject,damage=10,**extra))

    def death(self,subject='mob',killer='a',participants=None,**envelope):
        return self.send('combat_death',dict(source=combat_telemetry.ADAPTER,subject=subject,
            prefab='Boar',enemy='Boar',killer=killer,
            participants=participants if participants is not None else ['a','b','a']),**envelope)

    def combat(self):
        return telemetry.summary(self.db,100)['combat']

    def players(self):
        return {p['creator_id']:p for p in self.combat()['players']}

    def test_fatal_hit_and_participation_are_distinct_and_deduplicated(self):
        self.hit();self.hit('b','Bows',52)
        e=self.death()
        self.assertFalse(telemetry.accept(self.db,e))
        self.death()  # Duplicate callback with a different envelope.
        self.death(boot='restart')  # Same world and same creature identity.
        p=self.players()
        self.assertEqual((p['a']['kills'],p['a']['participation']),(1,1))
        self.assertEqual((p['b']['kills'],p['b']['participation']),(0,1))
        self.assertEqual(p['a']['enemies'][0]['kills'],1)
        self.assertEqual(p['b']['enemies'][0]['participation'],1)
        self.assertEqual(self.combat()['observed_deaths'],1)

    def test_highest_skill_is_chosen_independently_for_each_player(self):
        self.hit('a','Polearms',41);self.hit('a','Bows',16)
        self.hit('b','Bows',82);self.hit('b','Polearms',12)
        self.hit('a','Polearms',35)  # Later loss must not rewrite the observed high.
        p=self.players()
        self.assertEqual((p['a']['highest_skill']['skill'],p['a']['highest_skill']['level']),('Polearms',41))
        self.assertEqual((p['b']['highest_skill']['skill'],p['b']['highest_skill']['level']),('Bows',82))
        self.assertEqual(p['a']['skills'][0]['last_level'],35)
        self.assertEqual(p['a']['kills'],0)  # A hit is not a kill.

    def test_invalid_and_noncombat_skills_do_not_fabricate_a_skill(self):
        for skill,level in [('Run',99),('Pickaxes',75),('Bows',float('nan')),('Swords',101),('Clubs',-1)]:
            self.hit('a',skill,level)
        self.assertIsNone(self.players()['a']['highest_skill'])
        self.hit('a','Clubs',0)
        self.assertEqual(self.players()['a']['highest_skill']['level'],0)

    def test_unreported_bow_level_is_unknown_without_rewriting_raw_evidence(self):
        self.hit('a','Bows',0)
        p=self.players()['a']
        self.assertIsNone(p['highest_skill'])
        self.assertEqual(p['skills'][0]['skill'],'Bows')
        self.assertIsNone(p['skills'][0]['level'])
        self.assertIsNone(p['skills'][0]['last_level'])
        raw=self.db.execute("SELECT max_level,last_level FROM telemetry_combat_skills WHERE creator='a' AND skill='Bows'").fetchone()
        self.assertEqual(tuple(raw),(0,0))

    def test_known_zero_skill_ranks_ahead_of_an_unreported_bow_level(self):
        self.hit('a','Bows',0);self.hit('a','Clubs',0)
        p=self.players()['a']
        self.assertEqual((p['highest_skill']['skill'],p['highest_skill']['level']),('Clubs',0))
        self.assertEqual([s['skill'] for s in p['skills']],['Clubs','Bows'])
        self.assertIsNone(p['skills'][1]['level'])

    def test_unreported_bow_hit_does_not_erase_a_positive_observed_level(self):
        self.hit('a','Bows',42);self.hit('a','Bows',0)
        p=self.players()['a']
        self.assertEqual(p['highest_skill']['level'],42)
        self.assertIsNone(p['highest_skill']['last_level'])

    def test_unknown_killer_does_not_award_a_guessed_kill(self):
        self.death(killer=None,participants=['a'])
        p=self.players()
        self.assertEqual((p['a']['kills'],p['a']['participation']),(0,1))
        self.assertEqual(self.combat()['unattributed_deaths'],1)
        self.assertEqual(p['b']['enemies'],[])

    def test_killer_is_a_participant_even_when_earlier_hits_are_missing(self):
        self.death(killer='b',participants=[])
        self.assertEqual((self.players()['b']['kills'],self.players()['b']['participation']),(1,1))

    def test_unconfirmed_sources_and_nonpositive_damage_are_ignored(self):
        for kind in ('combat_hit','combat_death'):
            self.send(kind,dict(subject='mob',creator='a',skill='Bows',skill_level=99))
        for damage in (0,-1,float('inf')):
            self.send('combat_hit',dict(source=combat_telemetry.ADAPTER,subject='mob',creator='a',
                skill='Bows',skill_level=99,damage=damage))
        self.assertIsNone(self.players()['a']['highest_skill'])
        self.assertEqual(self.combat()['observed_deaths'],0)

    def test_separate_enemy_types_and_unknown_creators_remain_visible(self):
        self.death(killer='unmatched',participants=['unmatched'])
        self.send('combat_death',dict(source=combat_telemetry.ADAPTER,subject='other',prefab='Wolf',
            enemy='Wolf',killer='a',participants=['a']))
        p=self.players()
        self.assertEqual(p['unmatched']['name'],'Creator unmatched')
        self.assertEqual(p['unmatched']['enemies'][0]['enemy'],'Boar')
        self.assertEqual(p['a']['enemies'][0]['enemy'],'Wolf')

    def test_coverage_and_aggregates_survive_raw_event_retention(self):
        self.send('boot',dict(combat_adapter=combat_telemetry.ADAPTER),ts=1)
        self.hit();self.death()
        self.db.execute('DELETE FROM telemetry_events')
        c=self.combat()
        self.assertEqual(c['started'],1)
        self.assertTrue(c['active'])
        self.assertEqual(c['kills'],1)
        self.assertEqual(self.players()['a']['highest_skill']['level'],41)

    def test_world_rollback_starts_separate_combat_totals(self):
        self.hit();self.death()
        self.send('boot',dict(combat_adapter=combat_telemetry.ADAPTER),boot='restored',clock=10)
        self.assertEqual(self.combat()['kills'],0)
        self.death(boot='restored',clock=10)
        self.assertEqual(self.combat()['kills'],1)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telemetry_combat_deaths').fetchone()[0],2)

    def test_bad_death_is_quarantined_without_stalling_following_records(self):
        bad=self.event('combat_death',dict(source=combat_telemetry.ADAPTER,subject='mob',prefab='Boar',
            enemy='Boar',participants=['a']*65))
        good=self.event('plant',dict(creator='a',subject='p',planted_at='1',plant='Carrot',category='crop'))
        (self.root/'events.jsonl').write_text(json.dumps(bad)+'\n'+json.dumps(good)+'\n')
        telemetry.ingest(self.db,self.root)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM telemetry_quarantine').fetchone()[0],1)
        self.assertEqual(self.metric()['planted'],1)


if __name__=='__main__':unittest.main()
