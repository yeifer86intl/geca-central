"""Excepción temporal GECA 2026. Nunca escribe en attention_alerts (oficial).
Las fechas disponibles no consecutivas son admisibles solo hasta 2026-12-31.
Registra ventanas exactas, huecos y motivos de rechazo para auditoría.
"""
import datetime as dt
import json
import os
from collections import defaultdict

UTC = dt.timezone.utc
START = dt.date(2026, 11, 1)
END = dt.date(2026, 12, 31)
# Solo experimental: cambiar a 14 cuando se desee. NO modifica la regla oficial.
EXPERIMENTAL_DAYS = int(os.getenv('GECA_EXPERIMENTAL_DAYS', '2'))
if not 2 <= EXPERIMENTAL_DAYS <= 60:
    raise ValueError('GECA_EXPERIMENTAL_DAYS debe estar entre 2 y 60')
DDL = '''
CREATE TABLE IF NOT EXISTS geca_experimental_evaluations (
  evaluated_utc_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL,
  mode TEXT NOT NULL DEFAULT 'experimental_2026',
  dates_json TEXT NOT NULL, missing_calendar_days INTEGER NOT NULL,
  prior_avg REAL, recent_avg REAL, delta_viewers REAL, growth_pct REAL,
  eligible INTEGER NOT NULL, alerted INTEGER NOT NULL, reason TEXT NOT NULL,
  checked_utc TEXT NOT NULL,
  PRIMARY KEY(evaluated_utc_day,twitch_game_id)
);
CREATE TABLE IF NOT EXISTS geca_experimental_alerts (
  evaluated_utc_day TEXT NOT NULL, twitch_game_id TEXT NOT NULL,
  dates_json TEXT NOT NULL, missing_calendar_days INTEGER NOT NULL,
  prior_avg REAL NOT NULL, recent_avg REAL NOT NULL,
  delta_viewers REAL NOT NULL, growth_pct REAL NOT NULL,
  PRIMARY KEY(evaluated_utc_day,twitch_game_id)
);
'''

def _select_one_per_day(snapshots, day):
    """Consistent daily slot: 23:00 UTC ±90 min; latest delay belongs to prior day.
    Returns (viewer_count, timestamp) or None. Ignore incomplete captures.
    """
    target = dt.datetime.combine(day, dt.time(23, 0), tzinfo=UTC)
    candidates = []
    for row in snapshots:
        try:
            stamp = dt.datetime.fromisoformat(row['captured_utc'].replace('Z', '+00:00')).astimezone(UTC)
            diff = abs((stamp-target).total_seconds()) / 60
            if diff <= 90 and row['streams_complete'] == 1:
                candidates.append((diff,stamp,float(row['viewers_observed'])))
        except (TypeError, ValueError, AttributeError):
            continue
    if not candidates:
        return None
    diff,stamp,value=min(candidates,key=lambda x:(x[0],x[1]))
    return value,stamp

def evaluate_experimental(db, asof=None, include_proposals=True):
    asof = asof or dt.datetime.now(UTC).date()
    if isinstance(asof,str): asof=dt.date.fromisoformat(asof)
    db.executescript(DDL)
    if not START <= asof <= END:
        # Historical rows remain permanently auditable, no new experimental alerts.
        return {'mode':'official_only','evaluated':0,'alerts':0,'reason':'outside_experimental_2026'}
    checked=dt.datetime.now(UTC).isoformat(timespec='seconds')
    day_text=asof.isoformat()
    # Avoid using today's partial captures; evaluate the configured number of latest observed days before asof.
    allrows=db.execute('''SELECT twitch_game_id,captured_utc,viewers_observed,streams_complete
      FROM game_snapshots WHERE substr(captured_utc,1,10) <= ?
      ORDER BY twitch_game_id,captured_utc''',(day_text,)).fetchall()
    games=defaultdict(list)
    for row in allrows: games[row['twitch_game_id']].append(row)
    db.execute('DELETE FROM geca_experimental_alerts WHERE evaluated_utc_day=?',(day_text,))
    db.execute('DELETE FROM geca_experimental_evaluations WHERE evaluated_utc_day=?',(day_text,))
    emitted=[]
    for gid, rows in games.items():
        candidates={}
        # Session day can include the following day's 00:00–00:30 UTC.
        session_days=set()
        for row in rows:
            try:
                stamp=dt.datetime.fromisoformat(row['captured_utc'].replace('Z','+00:00')).astimezone(UTC)
                for d in (stamp.date(),stamp.date()-dt.timedelta(days=1)):
                    if START <= d < asof:
                        session_days.add(d)
            except (ValueError,TypeError,AttributeError):pass
        for day in sorted(session_days):
            selection=_select_one_per_day(rows,day)
            if selection is not None: candidates[day]=selection
        selected=sorted(candidates)[-EXPERIMENTAL_DAYS:]
        dates=[d.isoformat() for d in selected]
        missing=(selected[-1]-selected[0]).days+1-len(selected) if selected else 0
        prior=recent=delta=growth=None
        reason='insufficient_observed_days';eligible=alerted=0
        if len(selected)==EXPERIMENTAL_DAYS:
            offsets=[(candidates[d][1]-dt.datetime.combine(d,dt.time(23),tzinfo=UTC)).total_seconds()/60 for d in selected]
            if max(offsets)-min(offsets)>90:
                reason='non_comparable_capture_times'
            else:
                values=[candidates[d][0] for d in selected]
                prior_count=EXPERIMENTAL_DAYS//2
                recent_count=EXPERIMENTAL_DAYS-prior_count
                prior=sum(values[:prior_count])/prior_count
                recent=sum(values[prior_count:])/recent_count
                if prior<=0:reason='zero_previous_average'
                else:
                    eligible=1;delta=recent-prior;growth=100*delta/prior
                    alerted=int(delta>=500 and growth>=50)
                    reason='alert' if alerted else 'threshold_not_met'
        db.execute('''INSERT INTO geca_experimental_evaluations VALUES
          (?,?,?,?,?,?,?,?,?,?,?,?,?)''',(day_text,str(gid),'experimental_2026',json.dumps(dates),missing,
          prior,recent,delta,growth,eligible,alerted,reason,checked))
        if alerted:
            db.execute('''INSERT INTO geca_experimental_alerts VALUES(?,?,?,?,?,?,?,?)''',
                (day_text,str(gid),json.dumps(dates),missing,prior,recent,delta,growth))
            emitted.append((gid,prior,recent,delta,growth,dates,missing))
    if include_proposals:
        # Official proposals are never overwritten. No more than 3 TOTAL per day.
        existing=db.execute('SELECT COUNT(*) FROM editorial_proposals WHERE proposal_day=?',(day_text,)).fetchone()[0]
        room=max(0,3-existing)
        for gid,prior,recent,delta,growth,dates,missing in sorted(emitted,key=lambda x:(-x[3],str(x[0]))):
            match=db.execute("SELECT steam_appid FROM game_matches WHERE twitch_game_id=? AND status='matched' ORDER BY steam_appid LIMIT 1",(gid,)).fetchone()
            key='steam:'+str(match[0]) if match else 'twitch:'+str(gid)
            evidence={'mode':'experimental_2026','official_pdf_compliant':False,'source':'Twitch',
                      'rule':'attention_growth_experimental','observed_dates_utc':dates,
                      'configured_days':EXPERIMENTAL_DAYS,'previous_window_days':EXPERIMENTAL_DAYS//2,'recent_window_days':EXPERIMENTAL_DAYS-EXPERIMENTAL_DAYS//2,'missing_days_between_observations':missing,'previous_avg_viewers':round(prior,2),
                      'recent_avg_viewers':round(recent,2),'delta_viewers':round(delta,2),
                      'growth_pct':round(growth,2),'scope':'23:00 UTC +- 90 min, complete streams',
                      'warning':'Alerta provisional; admite días no consecutivos'}
            db.execute('''INSERT OR REPLACE INTO geca_signals
              (proposal_day,game_key,rule,evidence_json,created_utc) VALUES (?,?,?,?,?)''',
              (day_text,key,'attention_growth_experimental',json.dumps(evidence,ensure_ascii=False),checked))
            if room:
                name=db.execute('SELECT twitch_name FROM twitch_games WHERE twitch_game_id=?',(gid,)).fetchone()
                db.execute('''INSERT OR IGNORE INTO editorial_proposals
                   (proposal_day,game_key,game_name,rule,evidence_json,question,status,created_utc)
                   VALUES (?,?,?,?,?,?,?,?)''',
                   (day_text,key,name[0] if name else str(gid),'attention_growth_experimental',
                    json.dumps(evidence,ensure_ascii=False),
                    '¿Merece revisión editorial esta señal EXPERIMENTAL, no oficial?', 'pending',checked))
                room-=1
    db.commit()
    return {'mode':'experimental_2026','configured_days':EXPERIMENTAL_DAYS,'evaluated':len(games),'alerts':len(emitted),
            'proposals_total':db.execute('SELECT count(*) FROM editorial_proposals WHERE proposal_day=?',(day_text,)).fetchone()[0]}
