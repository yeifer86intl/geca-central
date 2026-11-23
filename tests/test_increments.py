import gzip,json,sqlite3,tempfile,unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
from central import ingest,prepare
from geca_twitch.db import connect

class Increments(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.db=connect(Path(self.tmp.name)/'x.sqlite3');prepare(self.db);self.db.commit()
        self.addCleanup(self.db.close)
    def delta(self,events,run='1'):
        path=Path(self.tmp.name)/(run+'.jsonl.gz')
        with gzip.open(path,'wt',encoding='utf-8') as f:
            f.write(json.dumps({'_manifest':{'source':'repo/twitch','run_id':run}})+'\n')
            for item in events:f.write(json.dumps(item)+'\n')
        return path
    def test_replay(self):
        row={'twitch_game_id':'g1','twitch_name':'J','discovered_via':'test','updated_utc':'2026-11-23','payload_json':'{}'}
        p=self.delta([{'table':'twitch_games','row':row}])
        self.assertEqual(ingest(self.db,p,'repo/twitch','1'),1)
        self.assertEqual(ingest(self.db,p,'repo/twitch','1'),0)
        self.assertEqual(self.db.execute('select count(*) from twitch_games').fetchone()[0],1)
    def test_collision_rollback(self):
        self.db.execute("INSERT INTO game_snapshots VALUES ('c','g','2026-11-19T23:00:00Z',10,2,1,'r')");self.db.commit()
        row={'capture_id':'c','twitch_game_id':'g','captured_utc':'2026-11-19T23:00:00Z',
             'viewers_observed':999,'streams_observed':2,'streams_complete':1,'run_id':'r'}
        p=self.delta([{'table':'game_snapshots','row':row}])
        with self.assertRaisesRegex(RuntimeError,'inmutable'):
            ingest(self.db,p,'repo/twitch','1')
        self.assertEqual(self.db.execute('select viewers_observed from game_snapshots').fetchone()[0],10)
        self.assertEqual(self.db.execute('select count(*) from geca_applied_deltas').fetchone()[0],0)
    def test_wrong_manifest(self):
        p=self.delta([],run='invalid')
        with self.assertRaisesRegex(RuntimeError,'Manifiesto'):
            ingest(self.db,p,'repo/twitch','1')

if __name__=='__main__':unittest.main()
