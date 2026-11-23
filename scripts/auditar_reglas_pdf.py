"""Evalúa reglas directamente sobre la base histórica SIN modificarla."""
import argparse, json, sqlite3
from datetime import date
from pathlib import Path
from geca_twitch.pdf_rules import comparar_atencion, steam_proximo

def main():
    p=argparse.ArgumentParser()
    p.add_argument('--db',required=True)
    p.add_argument('--fecha',required=True,help='YYYY-MM-DD fecha UTC de corte')
    p.add_argument('--dias-experimentales',type=int,default=7)
    p.add_argument('--salida',default='outputs/auditoria_pdf.json')
    a=p.parse_args(); corte=date.fromisoformat(a.fecha)
    uri=Path(a.db).absolute().as_uri()+'?mode=ro'
    db=sqlite3.connect(uri,uri=True);db.row_factory=sqlite3.Row
    try:
        steam=[]; twitch=[]
        for r in db.execute('SELECT steam_appid,steam_name,release_date,verified_utc,payload_json FROM steam_games'):
            try: raw=json.loads(r['payload_json'] or '{}')
            except (ValueError,TypeError):raw={}
            coming=raw.get('coming_soon') is True or str(raw.get('coming_soon','')).lower()=='true'
            ok,razon=steam_proximo(coming,raw.get('ficha_estado',''),r['verified_utc'],corte,r['release_date'])
            if ok:steam.append({'appid':r['steam_appid'],'name':r['steam_name'],'razon':razon})
        ids=[r[0] for r in db.execute('SELECT DISTINCT twitch_game_id FROM game_snapshots')]
        for game_id in ids:
            rows=[dict(x) for x in db.execute('SELECT captured_utc, viewers_observed, streams_complete FROM game_snapshots WHERE twitch_game_id=? AND substr(captured_utc,1,10) <= ?',(game_id,corte.isoformat()))]
            oficial=comparar_atencion(rows,corte,'oficial')
            experimental=comparar_atencion(rows,corte,'experimental',a.dias_experimentales)
            if oficial.alerta or experimental.alerta:
                twitch.append({'game_id':game_id,'oficial':oficial.__dict__,'experimental':experimental.__dict__})
        result={'corte_utc':a.fecha,'pdf':{'steam_ficha_dias':7,'twitch_dias':14,
                'delta_min':500,'crecimiento_min_pct':50},'experimental_days':a.dias_experimentales,
                'steam_candidatos':steam,'twitch_alertas_detectadas':twitch,
                'nota':'Evaluación independiente en lectura. No modifica alertas ni decisiones.'}
        dest=Path(a.salida);dest.parent.mkdir(parents=True,exist_ok=True)
        dest.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps({'steam_candidatos':len(steam),'twitch_alertas':len(twitch),'archivo':str(dest)}))
    finally:db.close()
if __name__=='__main__':main()
