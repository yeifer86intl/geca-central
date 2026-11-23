import json
import unittest
from datetime import date
from geca_twitch.db import connect
from geca_twitch.engine import evaluate, parse_n, ensure_schema
from geca_twitch.pdf_rules import comparar_atencion

class N2Configuration(unittest.TestCase):
    def setUp(self):
        import tempfile
        from pathlib import Path
        self.t=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.t.name)/'t.sqlite3')
        ensure_schema(self.db)
        self.db.execute("INSERT INTO twitch_games(twitch_game_id,twitch_name,discovered_via,updated_utc,payload_json) VALUES('g','Juego','test','2026-11-01','{}')")
        self.db.commit()

    def tearDown(self):
        self.db.close()
        self.t.cleanup()

    def insert(self, second=1900, first=1000, complete=1, scope='v1', second_time='23:00:00'):
        self.db.execute('DELETE FROM game_snapshots')
        self.db.execute('DELETE FROM geca_capture_scope')
        for i, (day,value,hour) in enumerate([('2026-11-19',first,'23:00:00'),('2026-11-21',second,second_time)]):
            cid=str(i)
            self.db.execute('INSERT INTO game_snapshots VALUES(?,?,?,?,?,?,?)',(cid,'g',day+'T'+hour+'+00:00',value,1,complete,'test'))
            self.db.execute('INSERT INTO geca_capture_scope VALUES(?,?,?)',(cid,scope,'2026-11-21T23:00:00Z'))
        self.db.commit()

    def test_split_one_one_nonconsecutive(self):
        self.insert()
        r=evaluate(self.db,date(2026,11,23),2)
        self.assertEqual(r['experimental_alerts'],1)
        self.assertEqual(r['official_alerts'],0)
        data=json.loads(self.db.execute("SELECT result_json FROM geca_rule_alerts WHERE mode='experimental'").fetchone()[0])
        self.assertEqual(data['fechas'],['2026-11-19','2026-11-21'])
        self.assertEqual(data['incremento'],900)
        self.assertEqual(data['crecimiento_pct'],90)
        self.assertTrue(data['provisional'])
        self.assertEqual(data['configured_days'],2)

    def test_thresholds_both_required(self):
        self.insert(first=1000,second=1499)
        self.assertEqual(evaluate(self.db,date(2026,11,23),2)['experimental_alerts'],0)
        self.insert(first=1500,second=2100)
        self.assertEqual(evaluate(self.db,date(2026,11,23),2)['experimental_alerts'],0) # +600 pero +40%

    def test_repeated_does_not_duplicate(self):
        self.insert()
        evaluate(self.db,date(2026,11,23),2)
        evaluate(self.db,date(2026,11,23),2)
        self.assertEqual(self.db.execute("SELECT count(*) FROM geca_rule_alerts WHERE mode='experimental'").fetchone()[0],1)

    def test_different_capture_hour_rejected(self):
        self.insert(second_time='20:00:00')
        self.assertEqual(evaluate(self.db,date(2026,11,23),2)['experimental_alerts'],0)

    def test_year_change_official_unchanged(self):
        self.insert()
        self.assertEqual(evaluate(self.db,date(2027,1,1),2)['experimental_alerts'],0)

    def test_minimum_two(self):
        self.assertEqual(parse_n(2),2)
        with self.assertRaises(ValueError):parse_n(1)

    def test_rule_direct_n2(self):
        rows=[{'captured_utc':'2026-11-19T23:00:00Z','viewers_observed':1000,'streams_complete':1,'scope':'v1'},
              {'captured_utc':'2026-11-21T23:00:00Z','viewers_observed':1900,'streams_complete':1,'scope':'v1'}]
        r=comparar_atencion(rows,date(2026,11,23),'experimental',2)
        self.assertTrue(r.alerta)
        self.assertEqual(r.huecos,1)
        self.assertFalse(comparar_atencion(rows,date(2026,11,23),'oficial').valido)

if __name__=='__main__':unittest.main()
