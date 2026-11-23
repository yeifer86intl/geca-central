import tempfile,unittest,os,importlib
from pathlib import Path
from datetime import date,datetime,time,timezone
from geca_twitch.db import connect
os.environ["GECA_EXPERIMENTAL_DAYS"]="14"
import geca_twitch.experimental as experimental
from geca_twitch.experimental import evaluate_experimental

class Experimental2026Test(unittest.TestCase):
    def setUp(self):
        self.old_days=experimental.EXPERIMENTAL_DAYS
        experimental.EXPERIMENTAL_DAYS=14
        self.tmp=tempfile.TemporaryDirectory()
        self.db=connect(Path(self.tmp.name)/'geca.sqlite3')
        self.db.execute("INSERT INTO twitch_games (twitch_game_id,twitch_name,discovered_via,updated_utc,payload_json) VALUES ('1','Juego de prueba','test','2026-11-20','{}')")
        days=[1,2,3,4,7,8,9,10,11,12,13,14,15,16]
        for ix,day in enumerate(days):
            d=date(2026,11,day).isoformat()
            self.db.execute('INSERT INTO game_snapshots VALUES (?,?,?,?,?,?,?)',
                            (f'cap{ix}','1',f'{d}T23:00:00+00:00',800 if ix<7 else 1500,3,1,'test'))
        self.db.commit()
    def tearDown(self):
        experimental.EXPERIMENTAL_DAYS=self.old_days
        self.db.close();self.tmp.cleanup()
    def test_experiment_and_exact_evidence(self):
        result=evaluate_experimental(self.db,date(2026,11,20))
        self.assertEqual(result['alerts'],1)
        r=self.db.execute('SELECT missing_calendar_days,delta_viewers,growth_pct FROM geca_experimental_alerts').fetchone()
        self.assertEqual(r[0],2);self.assertEqual(r[1],700)
        self.assertAlmostEqual(r[2],87.5)
        self.assertEqual(self.db.execute('SELECT count(*) FROM attention_alerts').fetchone()[0],0)
        self.assertEqual(result['proposals_total'],1)
        again=evaluate_experimental(self.db,date(2026,11,20))
        self.assertEqual(again['alerts'],1)
        self.assertEqual(self.db.execute('SELECT count(*) FROM geca_experimental_alerts').fetchone()[0],1)
    def test_automatic_expiry_keeps_history(self):
        evaluate_experimental(self.db,date(2026,11,20))
        result=evaluate_experimental(self.db,date(2027,1,1))
        self.assertEqual(result['mode'],'official_only')
        self.assertEqual(self.db.execute('SELECT count(*) FROM geca_experimental_alerts').fetchone()[0],1)
    def test_fewer_than_14(self):
        self.db.execute("DELETE FROM game_snapshots WHERE capture_id='cap0'");self.db.commit()
        result=evaluate_experimental(self.db,date(2026,11,20))
        self.assertEqual(result['alerts'],0)
        self.assertEqual(self.db.execute('SELECT reason FROM geca_experimental_evaluations').fetchone()[0],'insufficient_observed_days')
if __name__=='__main__':unittest.main()
