"""Descarga deltas públicos desde GitHub Actions y aplica en orden de run; recuperable."""
import argparse,json,os,subprocess,tempfile
from pathlib import Path
from central import ingest,prepare
from geca_twitch.db import connect

def sources():
    return [(os.environ.get('GECA_STEAM_REPO','yeifergeca/geca-radar'),'geca-steam-delta'),
            (os.environ.get('GECA_TWITCH_REPO','yeifergeca/geca-twitch'),'geca-twitch-delta')]

def call(args):
    proc=subprocess.run(['gh',*args],text=True,capture_output=True,encoding='utf-8')
    if proc.returncode:raise RuntimeError(' '.join(args)+'\n'+proc.stderr)
    return proc.stdout

def main(db,limit,require_new):
    connection=connect(db)
    prepare(connection);connection.commit()
    applied=0;errors=[]
    try:
        for repo,artifact_name in sources():
            # El rango de búsqueda se limita a ejecuciones disponibles, nunca a datos históricos de SQLite.
            data=json.loads(call(['run','list','-R',repo,'--limit',str(limit),
                                  '--json','databaseId,conclusion,createdAt']))
            runs=sorted((x for x in data if x['conclusion']=='success'),key=lambda r:r['createdAt'])
            for r in runs:
                rid=str(r['databaseId'])
                if connection.execute('SELECT 1 FROM geca_applied_deltas WHERE source=? AND run_id=?',(repo,rid)).fetchone():
                    continue
                # Las ejecuciones anteriores a instalar el protocolo no tienen delta.
                artifacts=json.loads(call(['api',f'repos/{repo}/actions/runs/{rid}/artifacts']))['artifacts']
                present=any(x['name']==artifact_name and not x['expired'] for x in artifacts)
                if not present:
                    print('Sin delta publicado:',repo,rid);continue
                with tempfile.TemporaryDirectory() as tmp:
                    call(['run','download',rid,'-R',repo,'--name',artifact_name,'--dir',tmp])
                    files=list(Path(tmp).rglob('*.jsonl.gz'))
                    if len(files)!=1:raise RuntimeError('Artefacto inesperado: '+repo+' '+rid)
                    applied+=ingest(connection,files[0],repo,rid)
        if require_new and not applied:raise RuntimeError('No se incorporaron datos nuevos: no publicar actualización vacía')
        print('Eventos incorporados:',applied)
    finally:connection.close()

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--db',default='state/geca.sqlite3')
    p.add_argument('--limit',type=int,default=100);p.add_argument('--require-new',action='store_true')
    a=p.parse_args();main(a.db,a.limit,a.require_new)
