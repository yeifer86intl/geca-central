"""Reportes ligeros de las reglas GECA; evita exportar históricos multimedia gigantes."""
import csv
from pathlib import Path

def export_csv(db,folder,asof):
    root=Path(folder);root.mkdir(parents=True,exist_ok=True)
    reports={
      'evaluaciones_reglas.csv':('''SELECT e.evaluation_day,e.twitch_game_id,t.twitch_name,e.mode,
             e.configured_days,e.result_json,e.evaluated_utc
             FROM geca_rule_evaluations e LEFT JOIN twitch_games t USING(twitch_game_id)
             WHERE e.evaluation_day=? ORDER BY e.mode,e.twitch_game_id''',(asof,)),
      'alertas_reglas.csv':('''SELECT a.evaluation_day,a.twitch_game_id,t.twitch_name,a.mode,
             a.configured_days,a.result_json FROM geca_rule_alerts a
             LEFT JOIN twitch_games t USING(twitch_game_id)
             WHERE a.evaluation_day=? ORDER BY a.mode,a.twitch_game_id''',(asof,)),
      'senales_geca.csv':('''SELECT evaluation_day,game_key,rule,mode,evidence_json
             FROM geca_rule_signals WHERE evaluation_day=? ORDER BY game_key''',(asof,)),
      'propuestas_geca.csv':('''SELECT proposal_day,game_key,game_name,rule,evidence_json,question,status
             FROM editorial_proposals WHERE proposal_day=? ORDER BY game_key''',(asof,)),
      'decisiones_humanas.csv':('''SELECT id,proposal_day,game_key,decision,note,decided_by,decided_utc
             FROM editorial_decisions ORDER BY decided_utc DESC''',()),
      'cola_humana.csv':('''SELECT d.id,d.proposal_day,d.game_key,d.note,d.decided_by,d.decided_utc
             FROM editorial_decisions d WHERE d.decision='analizar' AND NOT EXISTS(
               SELECT 1 FROM editorial_decisions newer WHERE newer.proposal_day=d.proposal_day
               AND newer.game_key=d.game_key AND (newer.decided_utc>d.decided_utc
               OR (newer.decided_utc=d.decided_utc AND newer.id>d.id)))''',()),
    }
    for filename,(query,args) in reports.items():
        cursor=db.execute(query,args)
        with (root/filename).open('w',newline='',encoding='utf-8-sig') as f:
            writer=csv.writer(f);writer.writerow([col[0] for col in cursor.description]);writer.writerows(cursor)
