import os, tempfile, unittest, importlib
from pathlib import Path
from datetime import date
from geca_twitch.db import connect
import geca_twitch.experimental as exp

class Window7Test(unittest.TestCase):
    def test_seven_days_splits_three_and_four(self):
        old=exp.EXPERIMENTAL_DAYS
        exp.EXPERIMENTAL_DAYS=7
        try:
            with tempfile.TemporaryDirectory() as td:
                db=connect(Path(td)/"t.sqlite3")
                db.execute("INSERT INTO twitch_games(twitch_game_id,twitch_name,discovered_via,updated_utc,payload_json) VALUES ('1','Test','test','2026-11-20','{}')")
                for i,d in enumerate([1,2,4,5,7,8,9]):
                    db.execute('INSERT INTO game_snapshots VALUES (?,?,?,?,?,?,?)', (f'c{i}','1',f'2026-11-{d:02d}T23:00:00+00:00',800 if i<3 else 1500,3,1,'test'))
                db.commit()
                r=exp.evaluate_experimental(db,date(2026,11,20))
                self.assertEqual(r['alerts'],1)
                self.assertEqual(r['configured_days'],7)
                a=db.execute('SELECT dates_json,missing_calendar_days FROM geca_experimental_alerts').fetchone()
                self.assertEqual(a[1],2)
                self.assertEqual(len(__import__('json').loads(a[0])),7)
                db.close()
        finally:
            exp.EXPERIMENTAL_DAYS=old
if __name__=='__main__': unittest.main()
