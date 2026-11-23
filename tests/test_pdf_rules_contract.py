import unittest
from datetime import date,datetime,timedelta,timezone
from geca_twitch.pdf_rules import steam_proximo, comparar_atencion, propuestas

class PDFContract(unittest.TestCase):
 def test_steam_seven_day_boundary(self):
  cutoff=date(2026,11,20)
  self.assertTrue(steam_proximo(True,'ok','2026-11-13T09:00:00Z',cutoff)[0])
  self.assertFalse(steam_proximo(True,'ok','2026-11-12T09:00:00Z',cutoff)[0])
  self.assertFalse(steam_proximo(False,'ok','2026-11-20T09:00:00Z',cutoff)[0])
 def sample(self,days,prior,recent,scope='base'):
  values=[]
  for i,d in enumerate(days):
   values.append({'captured_utc':d.isoformat()+'T23:00:00Z','viewers_observed':prior if i<len(days)//2 else recent,'streams_complete':1,'scope':scope})
  return values
 def test_official_thresholds(self):
  corte=date(2026,12,1); days=[corte-timedelta(days=i) for i in range(14,0,-1)]
  r=comparar_atencion(self.sample(days,800,1300),corte)
  self.assertTrue(r.alerta);self.assertEqual(r.incremento,500)
  self.assertAlmostEqual(r.crecimiento_pct,62.5)
  self.assertFalse(comparar_atencion(self.sample(days,1000,1499),corte).alerta)
 def test_missing_day_official_reject_experimental_accept(self):
  corte=date(2026,11,30);days=[date(2026,11,d) for d in [10,11,12,14,15,16,19]]
  obs=self.sample(days,200,900)
  self.assertFalse(comparar_atencion(obs,corte,'oficial').valido)
  self.assertTrue(comparar_atencion(obs,corte,'experimental',7).alerta)
 def test_missing_scope_or_incomplete(self):
  corte=date(2026,12,1); days=[corte-timedelta(days=i) for i in range(14,0,-1)]
  obs=self.sample(days,200,900);obs[3]['scope']='different'
  self.assertEqual(comparar_atencion(obs,corte).motivo,'alcance_no_comparable')
  obs=self.sample(days,200,900);obs[3]['streams_complete']=0
  self.assertFalse(comparar_atencion(obs,corte).valido)
 def test_experimental_expires(self):
  self.assertEqual(comparar_atencion([],date(2027,1,1),'experimental',7).motivo,'fuera_excepcion_2026')
 def test_proposals_unique_max3(self):
  signals=[{'game_key':'steam:'+str(i),'game_name':str(i),'rule':'steam','evidence':{}} for i in range(10)]
  self.assertEqual(len(propuestas(signals)),3)
if __name__=='__main__':unittest.main()
