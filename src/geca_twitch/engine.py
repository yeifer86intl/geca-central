"""Motor GECA v2: PDF oficial y excepción controlada, con evidencia reproducible.
No borra decisiones editoriales. Las alertas experimentales NO son oficiales.
"""
import datetime as dt
import json
import os
from collections import defaultdict
from .pdf_rules import comparar_atencion
from .releases import get_launch_candidates

UTC=dt.timezone.utc
DDL='''
CREATE TABLE IF NOT EXISTS geca_capture_scope(
 capture_id TEXT PRIMARY KEY, scope_id TEXT NOT NULL, documented_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS geca_rule_evaluations(
 evaluation_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL, mode TEXT NOT NULL,
 configured_days INTEGER NOT NULL, result_json TEXT NOT NULL, evaluated_utc TEXT NOT NULL,
 PRIMARY KEY(evaluation_day,twitch_game_id,mode,configured_days));
CREATE TABLE IF NOT EXISTS geca_rule_alerts(
 evaluation_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL, mode TEXT NOT NULL,
 configured_days INTEGER NOT NULL, result_json TEXT NOT NULL,
 PRIMARY KEY(evaluation_day,twitch_game_id,mode,configured_days));
CREATE TABLE IF NOT EXISTS geca_rule_signals(
 evaluation_day TEXT NOT NULL, game_key TEXT NOT NULL, rule TEXT NOT NULL,
 mode TEXT NOT NULL, evidence_json TEXT NOT NULL,
 PRIMARY KEY(evaluation_day,game_key,rule,mode));
CREATE TABLE IF NOT EXISTS geca_editorial_queue(
 proposal_day TEXT NOT NULL,game_key TEXT NOT NULL,decision_id INTEGER NOT NULL UNIQUE,
 created_utc TEXT NOT NULL,PRIMARY KEY(proposal_day,game_key,decision_id));
'''

def ensure_schema(db):
    db.executescript(DDL)

def parse_n(raw=None):
    if raw is None:raw=os.getenv('GECA_EXPERIMENTAL_DAYS','2')
    n=int(raw)
    if not 2<=n<=60:raise ValueError('GECA_EXPERIMENTAL_DAYS debe estar entre 2 y 60')
    return n

def pick_sessions(rows, asof, n, mode):
    """Selecciona captura más próxima a 23:00 UTC por sesión, sin duplicados diarios.
    Un retraso de 00:30 UTC corresponde al día calendario anterior.
    """
    selected={}
    for row in rows:
        try:
            stamp=dt.datetime.fromisoformat(row['captured_utc'].replace('Z','+00:00'))
            if stamp.tzinfo is None:continue
            stamp=stamp.astimezone(UTC)
            if row['streams_complete']!=1:continue
            for day in [stamp.date(),stamp.date()-dt.timedelta(days=1)]:
                if day>=asof:continue
                target=dt.datetime.combine(day,dt.time(23),tzinfo=UTC)
                offset=abs((stamp-target).total_seconds())/60
                if offset>90:continue
                current=selected.get(day)
                cand=(offset,stamp,row)
                if current is None or cand[:2]<current[:2]:selected[day]=cand
        except (KeyError,TypeError,ValueError,AttributeError):continue
    if mode=='oficial':
        dates=[asof-dt.timedelta(days=i) for i in range(14,0,-1)]
    else:
        dates=sorted(selected)[-n:]
    return dates,selected

def evaluate(db,asof=None,experimental_days=None):
    ensure_schema(db)
    asof=asof or dt.datetime.now(UTC).date()
    if isinstance(asof,str):asof=dt.date.fromisoformat(asof)
    n=parse_n(experimental_days)
    active=dt.date(2026,11,1)<=asof<=dt.date(2026,12,31)
    now=dt.datetime.now(UTC).isoformat(timespec='seconds')
    daytext=asof.isoformat()
    ids=[x[0] for x in db.execute('SELECT DISTINCT twitch_game_id FROM game_snapshots')]
    stats={'asof':daytext,'experimental_days':n,'official_alerts':0,'experimental_alerts':0,'steam_signals':0,'proposals_today':0,'official_evaluable':0,'experimental_evaluable':0,'scope_missing_official':0}
    signals=[]
    db.execute('SAVEPOINT geca_evaluation')
    try:
        for gid in ids:
            rows=[dict(x) for x in db.execute('''SELECT gs.capture_id,gs.captured_utc,gs.viewers_observed,gs.streams_complete,
                     sc.scope_id FROM game_snapshots gs LEFT JOIN geca_capture_scope sc USING(capture_id)
                     WHERE gs.twitch_game_id=? AND substr(gs.captured_utc,1,10)>=? AND substr(gs.captured_utc,1,10)<=?
                     ORDER BY gs.captured_utc''',(gid,(asof-dt.timedelta(days=125)).isoformat(),daytext))]
            for mode,window in [('oficial',14)]+([('experimental',n)] if active else []):
                days, chosen=pick_sessions(rows,asof,window,mode)
                chosen_rows=[]; missing_scope=False
                for d in days:
                    if d in chosen:
                        row=chosen[d][2]
                        scope=row['scope_id']
                        if not scope:missing_scope=True
                        chosen_rows.append({'session_day':d.isoformat(),'captured_utc':row['captured_utc'],'viewers_observed':row['viewers_observed'],
                                            'streams_complete':row['streams_complete'],
                                            'scope':scope if scope else 'legacy_no_verificado'})
                result=comparar_atencion(chosen_rows,asof,modo=mode,dias_experimentales=window)
                # PDF requiere igualdad comprobable de alcance: ausencia de metadatos invalida solo la modalidad oficial.
                if mode=='oficial' and missing_scope and result.valido:
                    from dataclasses import replace
                    result=replace(result,valido=False,alerta=False,motivo='alcance_no_documentado')
                    stats['scope_missing_official']+=1
                from dataclasses import asdict
                evidence=asdict(result)
                evidence.update({'scope_verified':not missing_scope,
                                 'provisional':mode=='experimental',
                                 'methodology':'PDF_2026_10_19' if mode=='oficial' else 'GECA_EXCEPTION_NOV_DEC_2026',
                                 'configured_days':window,
                                 'sampling_convention':'23:00 UTC ±90 min (decisión operativa)'})
                serialized=json.dumps(evidence,ensure_ascii=False,sort_keys=True)
                db.execute('''INSERT INTO geca_rule_evaluations VALUES (?,?,?,?,?,?)
                   ON CONFLICT(evaluation_day,twitch_game_id,mode,configured_days)
                   DO UPDATE SET result_json=excluded.result_json,evaluated_utc=excluded.evaluated_utc''',
                   (daytext,str(gid),mode,window,serialized,now))
                # Recalculable: eliminar solo alerta asociada a ESTA modalidad, ventana y fecha.
                db.execute('DELETE FROM geca_rule_alerts WHERE evaluation_day=? AND twitch_game_id=? AND mode=? AND configured_days=?',
                           (daytext,str(gid),mode,window))
                if result.valido:stats[mode.replace('oficial','official')+'_evaluable' if mode=='oficial' else 'experimental_evaluable']+=1
                if result.alerta:
                    db.execute('INSERT INTO geca_rule_alerts VALUES(?,?,?,?,?)',(daytext,str(gid),mode,window,serialized))
                    stats['official_alerts' if mode=='oficial' else 'experimental_alerts']+=1
                    match=db.execute("SELECT steam_appid FROM game_matches WHERE twitch_game_id=? AND status='matched' ORDER BY steam_appid LIMIT 1",(gid,)).fetchone()
                    game_key='steam:'+str(match[0]) if match else 'twitch:'+str(gid)
                    name=db.execute('SELECT twitch_name FROM twitch_games WHERE twitch_game_id=?',(gid,)).fetchone()
                    signals.append({'game_key':game_key,'name':name[0] if name else str(gid),
                                    'rule':'twitch_attention','mode':mode,'evidence':evidence,
                                    'score':float(result.incremento or 0)})
        for item in get_launch_candidates(db,asof):
            stats['steam_signals']+=1
            signals.append({'game_key':'steam:'+str(item['steam_appid']),'name':item['steam_name'],
                            'rule':'steam_upcoming','mode':'oficial',
                            'evidence':{'source':'Steam','verified_utc':item['verified_utc'],
                                        'release_date':item['release_date'],'days_until_release':item['days_until_release']},
                            'score':0.0})
        for signal in signals:
            db.execute('''INSERT INTO geca_rule_signals VALUES(?,?,?,?,?)
              ON CONFLICT(evaluation_day,game_key,rule,mode) DO UPDATE SET evidence_json=excluded.evidence_json''',
              (daytext,signal['game_key'],signal['rule'],signal['mode'],json.dumps(signal['evidence'],ensure_ascii=False)))
        # Se proponen hasta 3 juegos únicos EN TOTAL, sin eliminar decisiones históricas.
        existing=db.execute('SELECT count(DISTINCT game_key) FROM editorial_proposals WHERE proposal_day=?',(daytext,)).fetchone()[0]
        occupied={r[0] for r in db.execute('SELECT DISTINCT game_key FROM editorial_proposals WHERE proposal_day=?',(daytext,))}
        ranked=sorted(signals,key=lambda s:(s['mode']!='oficial',s['rule']!='steam_upcoming',-s['score'],s['game_key']))
        room=max(0,3-existing)
        for signal in ranked:
            key=signal['game_key']
            if not room or key in occupied:continue
            ev={'mode':signal['mode'],'rule':signal['rule'],'evidence':signal['evidence']}
            db.execute('''INSERT OR IGNORE INTO editorial_proposals
              (proposal_day,game_key,game_name,rule,evidence_json,question,status,created_utc)
              VALUES(?,?,?,?,?,?,?,?)''',(daytext,key,signal['name'],signal['rule']+'_'+signal['mode'],
                  json.dumps(ev,ensure_ascii=False),
                  '¿Merece revisión editorial esta evidencia? Validar encaje y derechos.', 'pending',now))
            occupied.add(key);room-=1
        stats['proposals_today']=len(occupied)
        db.execute('RELEASE SAVEPOINT geca_evaluation')
        db.commit()
    except BaseException:
        db.execute('ROLLBACK TO SAVEPOINT geca_evaluation')
        db.execute('RELEASE SAVEPOINT geca_evaluation')
        raise
    return stats
