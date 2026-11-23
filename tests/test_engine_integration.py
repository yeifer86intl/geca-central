import json
import tempfile
import unittest
from datetime import date,timedelta
from pathlib import Path
from geca_twitch.db import connect
from geca_twitch.engine import evaluate,ensure_schema

CUTOFF=date(2026,11,30)

class TestCentralEngine(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.t.name)/'test.sqlite3')
        ensure_schema(self.db)
        self.db.execute("INSERT INTO twitch_games(twitch_game_id,twitch_name,discovered_via,updated_utc,payload_json) VALUES('tg','Juego','test','2026-11-01','{}')")
        self.db.commit()
    def tearDown(self):
        self.db.close();self.t.cleanup()
    def add(self,days,early,late,scope='scope-v1',missing_scope=False,counts=None):
        self.db.execute('DELETE FROM game_snapshots')
        self.db.execute('DELETE FROM geca_capture_scope')
        split=len(days)//2
        for i,d in enumerate(days):
            val=counts[i] if counts is not None else (early if i<split else late)
            cid=f'cap{i}'
            self.db.execute('INSERT INTO game_snapshots VALUES(?,?,?,?,?,?,?)',
                            (cid,'tg',d.isoformat()+'T23:00:00+00:00',val,1,1,'test'))
            if not missing_scope:
                self.db.execute('INSERT INTO geca_capture_scope VALUES(?,?,?)',(cid,scope,'2026-11-20T23:00:00Z'))
        self.db.commit()
    def dayrange(self,n):
        return [CUTOFF-timedelta(days=n-i) for i in range(n)]
    def test_experiment_seven_and_ten_and_fourteen(self):
        for n in (2,7,10,14):
            days=[date(2026,11,d) for d in range(1,n+1)]
            self.add(days,700,1400)
            result=evaluate(self.db,CUTOFF,n)
            self.assertEqual(result['experimental_alerts'],1)
            self.assertEqual(result['official_alerts'],0)
            record=json.loads(self.db.execute('SELECT result_json FROM geca_rule_alerts WHERE mode="experimental" AND configured_days=?',(n,)).fetchone()[0])
            self.assertEqual(len(record['fechas']),n)
            self.assertEqual(record['dias_observados'],n)
            self.assertEqual(record['promedio_anterior'],700)
            self.assertEqual(record['promedio_reciente'],1400)
    def test_official_exact_and_proposals(self):
        self.add(self.dayrange(14),700,1400)
        result=evaluate(self.db,CUTOFF,7)
        self.assertEqual(result['official_alerts'],1)
        self.assertEqual(result['experimental_alerts'],0)  # últimas 7 observaciones ya están estabilizadas en 1400
        self.assertEqual(result['official_evaluable'],1)
        self.assertEqual(result['proposals_today'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM editorial_proposals').fetchone()[0],1)
        self.assertEqual(evaluate(self.db,CUTOFF,7)['proposals_today'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM editorial_proposals').fetchone()[0],1)
    def test_official_rejects_scope_unknown_and_experimental_labels_it(self):
        self.add(self.dayrange(14),700,1400,missing_scope=True)
        result=evaluate(self.db,CUTOFF,14)
        self.assertEqual(result['official_alerts'],0)
        self.assertEqual(result['experimental_alerts'],1)
        self.assertEqual(result['scope_missing_official'],1)
        record=json.loads(self.db.execute('SELECT result_json FROM geca_rule_alerts WHERE mode="experimental"').fetchone()[0])
        self.assertFalse(record['scope_verified'])
    def test_hole_in_official_but_experiment_works(self):
        days=self.dayrange(14)
        days[3]=date(2026,11,10)
        self.add(days,700,1400)
        result=evaluate(self.db,CUTOFF,14)
        self.assertEqual(result['official_alerts'],0)
        self.assertEqual(result['experimental_alerts'],1)
    def test_newyear_experiment_expires(self):
        self.add(self.dayrange(14),700,1400)
        evaluate(self.db,CUTOFF,7)
        previously=self.db.execute('SELECT count(*) FROM geca_rule_alerts WHERE mode="experimental"').fetchone()[0]
        result=evaluate(self.db,date(2027,1,1),7)
        self.assertEqual(result['experimental_alerts'],0)
        self.assertEqual(self.db.execute('SELECT count(*) FROM geca_rule_alerts WHERE mode="experimental"').fetchone()[0],previously)
    def test_retains_human_decision_and_queue(self):
        self.add(self.dayrange(14),700,1400)
        evaluate(self.db,CUTOFF,7)
        self.db.execute('INSERT INTO editorial_decisions (proposal_day,game_key,decision,decided_utc) VALUES(?,?,?,?)',(CUTOFF.isoformat(),'twitch:tg','analizar','2026-11-30T14:00:00Z'))
        self.db.commit()
        evaluate(self.db,CUTOFF,10)
        self.assertEqual(self.db.execute('SELECT COUNT(*) FROM editorial_decisions').fetchone()[0],1)
        self.assertLessEqual(self.db.execute('SELECT COUNT(DISTINCT game_key) FROM editorial_proposals WHERE proposal_day=?',(CUTOFF.isoformat(),)).fetchone()[0],3)
    def test_steam_only(self):
        self.db.execute("INSERT INTO steam_games VALUES ('42','Juego Steam','2027-03-01',0,0,0,'2026-11-29T15:00:00Z','{\"coming_soon\":true,\"ficha_estado\":\"ok\"}','2026-11-29T15:00:00Z')")
        self.db.commit()
        r=evaluate(self.db,CUTOFF,7)
        self.assertEqual(r['steam_signals'],1)
        self.assertEqual(r['proposals_today'],1)
    def test_no_false_alert_with_threshold(self):
        self.add(self.dayrange(14),1000,1499)
        r=evaluate(self.db,CUTOFF,14)
        self.assertEqual(r['official_alerts'],0)
        self.assertEqual(r['experimental_alerts'],0)
    def test_window_config_rejects_invalid(self):
        with self.assertRaises(ValueError):evaluate(self.db,CUTOFF,1)
    def test_scope_change_blocks_official(self):
        self.add(self.dayrange(14),700,1400)
        self.db.execute("UPDATE geca_capture_scope SET scope_id='different' WHERE capture_id='cap7'")
        self.db.commit()
        r=evaluate(self.db,CUTOFF,14)
        self.assertEqual(r['official_alerts'],0)
        self.assertEqual(r['experimental_alerts'],0)
if __name__=='__main__':unittest.main()
