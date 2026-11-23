"""GECA Central: restaura una semilla, aplica deltas idempotentes y evalúa el PDF.
Sin credenciales externas. Requiere PYTHONPATH=src y Python >=3.11.
"""
import argparse, gzip, hashlib, json, os, shutil, sqlite3
from datetime import datetime, timezone
from pathlib import Path
from geca_twitch.db import connect

TABLES={'steam_games','twitch_games','game_matches','match_candidates','runs',
        'stream_snapshots','game_snapshots','twitch_videos','video_observations',
        'twitch_clips','clip_observations','steam_observation_history','geca_capture_scope'}
IMMUTABLE={'stream_snapshots','game_snapshots','video_observations',
           'clip_observations','steam_observation_history','geca_capture_scope'}
KEEP_COUNTS={'stream_snapshots','game_snapshots','twitch_videos','twitch_clips',
             'video_observations','clip_observations','steam_observation_history','geca_capture_scope'}
FRESHNESS={'steam_games':'updated_utc','twitch_games':'updated_utc',
           'game_matches':'checked_utc','match_candidates':'checked_utc',
           'runs':'started_utc','twitch_videos':'last_seen_utc',
           'twitch_clips':'last_seen_utc'}

DDL='''
CREATE TABLE IF NOT EXISTS geca_applied_deltas(
 source TEXT NOT NULL, run_id TEXT NOT NULL, sha256 TEXT NOT NULL,
 applied_utc TEXT NOT NULL, event_count INTEGER NOT NULL,
 PRIMARY KEY(source,run_id));
CREATE TABLE IF NOT EXISTS geca_capture_scope(
 capture_id TEXT PRIMARY KEY,scope_id TEXT NOT NULL,documented_utc TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS steam_observation_history(
 source_run TEXT NOT NULL,steam_appid TEXT NOT NULL,observed_utc TEXT,
 evidence_json TEXT NOT NULL, PRIMARY KEY(source_run,steam_appid));
CREATE TABLE IF NOT EXISTS geca_merge_audit(
 id INTEGER PRIMARY KEY, source TEXT NOT NULL,run_id TEXT NOT NULL,table_name TEXT NOT NULL,
 primary_key_json TEXT NOT NULL,old_json TEXT NOT NULL,new_json TEXT NOT NULL,
 action TEXT NOT NULL,recorded_utc TEXT NOT NULL);
'''

def checked(db):
    x=db.execute('PRAGMA integrity_check').fetchone()[0]
    if x!='ok':raise RuntimeError('SQLite corrupto: '+str(x))
    if db.execute('PRAGMA foreign_key_check').fetchone():
        raise RuntimeError('Foreign keys inválidas')

def prepare(db):
    db.executescript(DDL)
    db.execute('PRAGMA busy_timeout=60000')
    checked(db)

def counts(db):
    return {t:db.execute(f'SELECT count(*) FROM "{t}"').fetchone()[0] for t in KEEP_COUNTS}

def seed(gz,target):
    target=Path(target)
    if target.exists():raise RuntimeError('La base ya existe: se prohíbe reinicializarla')
    target.parent.mkdir(parents=True,exist_ok=True)
    tmp=target.with_suffix('.seed.tmp')
    try:
        with gzip.open(gz,'rb') as src,open(tmp,'wb') as dst:shutil.copyfileobj(src,dst)
        db=sqlite3.connect(tmp)
        try:
            checked(db)
            if db.execute("SELECT count(*) FROM sqlite_master WHERE type='table' AND name='game_snapshots'").fetchone()[0]!=1:
                raise RuntimeError('No parece el histórico consolidado de Twitch')
            if db.execute('SELECT count(*) FROM game_snapshots').fetchone()[0]<10000:
                raise RuntimeError('La semilla no contiene las observaciones históricas esperadas')
            prepare(db);db.commit()
        finally:db.close()
        tmp.replace(target)
    finally:
        if tmp.exists():tmp.unlink()
    print('Semilla histórica restaurada:',target)

def primary_key(db,table):
    info=list(db.execute(f'PRAGMA table_info("{table}")'))
    keys=[r[1] for r in sorted(info,key=lambda v:v[5]) if r[5]>0]
    if not keys:raise RuntimeError('No hay clave primaria en '+table)
    return keys,{r[1] for r in info}

def ingest(db,filename,source,run):
    with open(filename,'rb') as delta_stream:
        h=hashlib.file_digest(delta_stream,'sha256').hexdigest()
    db.execute('BEGIN IMMEDIATE')
    try:
        existing=db.execute('SELECT sha256 FROM geca_applied_deltas WHERE source=? AND run_id=?',(source,str(run))).fetchone()
        if existing:
            if existing[0]!=h:raise RuntimeError(f'El run {run} cambió contenido: detener para auditoría')
            db.rollback();print('Delta ya aplicado:',source,run);return 0
        baseline=counts(db)
        n=0;seen_header=False
        with gzip.open(filename,'rt',encoding='utf-8') as f:
            for line in f:
                event=json.loads(line)
                if not seen_header:
                    header=event.get('_manifest')
                    if not header or header.get('source')!=source or str(header.get('run_id'))!=str(run):
                        raise RuntimeError('Manifiesto no corresponde al repositorio/run indicado')
                    seen_header=True;continue
                table=event.get('table'); row=event.get('row')
                if table not in TABLES or not isinstance(row,dict):raise ValueError('Evento inválido')
                keys,columns=primary_key(db,table)
                if not row or set(row)-columns or any(k not in row or row[k] is None for k in keys):
                    raise ValueError('Columnas/clave inválidas en '+table)
                where=' AND '.join(f'"{k}"=?' for k in keys)
                pk=[row[k] for k in keys]
                old=db.execute(f'SELECT * FROM "{table}" WHERE {where}',pk).fetchone()
                changed=old is not None and any(old[col]!=v for col,v in row.items())
                if changed and table in IMMUTABLE:
                    raise RuntimeError(f'Colisión de evento inmutable en {table} PK={pk}; rollback completo')
                if old is not None and not changed:continue
                cols=list(row)
                if changed:
                    old_dict=dict(old)
                    fresh=FRESHNESS.get(table)
                    update_allowed=(fresh is None or str(row.get(fresh) or '')>=str(old_dict.get(fresh) or ''))
                    db.execute('''INSERT INTO geca_merge_audit
                      (source,run_id,table_name,primary_key_json,old_json,new_json,action,recorded_utc)
                      VALUES(?,?,?,?,?,?,?,?)''',
                      (source,str(run),table,json.dumps(pk),json.dumps(old_dict,ensure_ascii=False),
                       json.dumps(row,ensure_ascii=False),'updated' if update_allowed else 'stale_skipped',
                       datetime.now(timezone.utc).isoformat()))
                    if not update_allowed:continue
                fields=','.join('"'+c+'"' for c in cols)
                binds=','.join('?' for _ in cols)
                if old is None:
                    db.execute(f'INSERT INTO "{table}" ({fields}) VALUES ({binds})',[row[c] for c in cols])
                else:
                    changes=[c for c in cols if c not in keys]
                    if changes:
                        sets=','.join(f'"{c}"=?' for c in changes)
                        db.execute(f'UPDATE "{table}" SET {sets} WHERE {where}',
                                   [row[c] for c in changes]+pk)
                n+=1
        if not seen_header:raise RuntimeError('Delta vacío sin manifiesto')
        after=counts(db)
        for table,before in baseline.items():
            if after[table]<before:raise RuntimeError('Pérdida de filas en '+table)
        db.execute('INSERT INTO geca_applied_deltas VALUES (?,?,?,?,?)',
                   (source,str(run),h,datetime.now(timezone.utc).isoformat(),n))
        db.commit()
        checked(db)
        print(json.dumps({'source':source,'run':run,'applied':n,'counts':after},ensure_ascii=False))
        return n
    except BaseException:
        db.rollback();raise

def evaluate(db,folder,asof=None,experimental_days=None):
    from geca_twitch.engine import evaluate as run_rules
    from geca_twitch.reporting import export_csv
    result=run_rules(db,asof,experimental_days)
    export_csv(db,folder,result["asof"])
    checked(db)
    Path(folder).mkdir(exist_ok=True,parents=True)
    (Path(folder)/'resumen_geca.json').write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False))

def main():
    p=argparse.ArgumentParser()
    p.add_argument('action',choices=['seed','apply','evaluate','verify'])
    p.add_argument('--db',default='state/geca.sqlite3')
    p.add_argument('--seed-file');p.add_argument('--delta');p.add_argument('--source');p.add_argument('--run')
    p.add_argument('--out',default='outputs')
    p.add_argument('--asof',help='fecha corte UTC YYYY-MM-DD; predeterminado hoy UTC')
    p.add_argument('--experimental-days',type=int,default=None)
    a=p.parse_args()
    if a.action=='seed':
        if not a.seed_file: p.error('--seed-file obligatorio')
        seed(a.seed_file,a.db);return
    if not Path(a.db).exists():raise RuntimeError('No existe base maestra; PROHIBIDO iniciar vacía')
    db=connect(a.db)
    try:
        prepare(db);db.commit()
        if a.action=='apply':
            if not all([a.delta,a.source,a.run]):p.error('Se necesitan --delta, --source y --run')
            ingest(db,a.delta,a.source,a.run)
        elif a.action=='evaluate':evaluate(db,a.out,a.asof,a.experimental_days)
        else:print(json.dumps({'integridad':'ok','counts':counts(db),'applied_deltas':db.execute('SELECT count(*) FROM geca_applied_deltas').fetchone()[0]},ensure_ascii=False))
    finally:db.close()

if __name__=='__main__':main()
