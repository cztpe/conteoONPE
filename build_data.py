"""
Genera UN JSON por snapshot ONPE incluyendo PERÚ + EXTRANJERO.

Uso:
    python build_data.py <SNAPSHOT_DIR> <OUT_DIR>

Donde:
    SNAPSHOT_DIR contiene totales.csv y candidatos.csv (generados por el scraper)
    OUT_DIR es donde se escribe el archivo onpe_data_<timestamp>_<avance>pct.json

Si no se pasan argumentos, usa rutas por defecto (sandbox).
"""
import sys, os, json
from datetime import datetime, timedelta
import pandas as pd

# --------- rutas ---------
if len(sys.argv) >= 3:
    SNAPSHOT_DIR = sys.argv[1]
    OUT_DIR = sys.argv[2]
else:
    # fallback: sandbox / local dev
    SNAPSHOT_DIR = '/mnt/user-data/uploads'
    OUT_DIR = '/mnt/user-data/outputs'

ROOT = '__root__'
EXT_VIRTUAL = '900000'

tot_path = os.path.join(SNAPSHOT_DIR, 'totales.csv')
cand_path = os.path.join(SNAPSHOT_DIR, 'candidatos.csv')

if not os.path.exists(tot_path) or not os.path.exists(cand_path):
    print(f'ERROR: no encuentro {tot_path} o {cand_path}', file=sys.stderr)
    sys.exit(1)

tot = pd.read_csv(tot_path, dtype={'ubigeo':str,'ubigeo_dep':str,'ubigeo_prov':str}, encoding='utf-8')
cand = pd.read_csv(cand_path, dtype={'ubigeo':str,'ubigeo_dep':str,'ubigeo_prov':str,'codigoAgrupacionPolitica':str}, encoding='utf-8')

for df in (tot, cand):
    for c in ['ubigeo','ubigeo_dep','ubigeo_prov']:
        if c in df.columns:
            df[c] = df[c].fillna('')

if 'ambito' not in tot.columns: tot['ambito'] = 1
if 'ambito' not in cand.columns: cand['ambito'] = 1
tot['ambito'] = tot['ambito'].fillna(1).astype(int)
cand['ambito'] = cand['ambito'].fillna(1).astype(int)

def is_ext_ub(ub): return ub and isinstance(ub,str) and ub.startswith('9')

def nkey(r):
    if r['nivel']=='nacional': return ROOT
    if r['nivel']=='ambito':
        return r['ubigeo'] if r.get('ambito')==2 else None
    return r['ubigeo']

tot['nkey'] = tot.apply(nkey, axis=1)
cand['nkey'] = cand.apply(nkey, axis=1)
tot_tree = tot[tot['nkey'].notna()].copy()
cand_tree = cand[cand['nkey'].notna()].copy()

snapshot_ts = tot['snapshot_utc'].iloc[0]
dt_utc = datetime.fromisoformat(snapshot_ts.replace('Z','+00:00'))
lima = dt_utc - timedelta(hours=5)
label = lima.strftime('%d %b %H:%M').lower()
avance = float(tot[tot['nivel']=='nacional']['actasContabilizadas'].iloc[0])

master = cand[cand['nivel']=='nacional'][['codigoAgrupacionPolitica','nombreAgrupacionPolitica','nombreCandidato']].drop_duplicates()
nac_v = cand[cand['nivel']=='nacional'].set_index('codigoAgrupacionPolitica')['totalVotosValidos'].to_dict()
master['_v'] = master['codigoAgrupacionPolitica'].map(nac_v).fillna(0)
master['_bn'] = master['nombreAgrupacionPolitica'].isin(['VOTOS EN BLANCO','VOTOS NULOS'])
master = master.sort_values(['_bn','_v'],ascending=[True,False]).reset_index(drop=True)
candidatos_master = [{'code':r['codigoAgrupacionPolitica'],'agr':r['nombreAgrupacionPolitica'],
                      'cand': r['nombreCandidato'] if isinstance(r['nombreCandidato'],str) else ''}
                     for _,r in master.iterrows()]

nodes = {}
nr = tot[tot['nivel']=='nacional'].iloc[0]
nodes[ROOT] = {'nombre': nr['nombre'], 'nivel': 'nacional', 'parent': None, 'ubigeo': '', 'ambito': 0}

for _, r in tot_tree.iterrows():
    if r['nivel']=='nacional': continue
    key = r['ubigeo']
    if r['nivel']=='ambito':
        parent = ROOT
    elif r['nivel']=='departamento':
        parent = EXT_VIRTUAL if (r['ambito']==2 or is_ext_ub(key)) else ROOT
    elif r['nivel']=='provincia':
        parent = r['ubigeo_dep']
    elif r['nivel']=='distrito':
        parent = r['ubigeo_prov']
    else:
        continue
    nodes[key] = {
        'nombre': r['nombre'],
        'nivel': r['nivel'],
        'parent': parent,
        'ubigeo': r['ubigeo'],
        'ambito': int(r['ambito']) if r['ambito'] else 1
    }

cm = {k: [] for k in nodes}
for k, n in nodes.items():
    p = n['parent']
    if p is not None and p in cm:
        cm[p].append(k)
for k in cm:
    cm[k].sort(key=lambda x: nodes[x]['nombre'])
if EXT_VIRTUAL in cm.get(ROOT, []):
    cm[ROOT].remove(EXT_VIRTUAL)
    cm[ROOT].append(EXT_VIRTUAL)
for k, n in nodes.items():
    n['children'] = cm[k]

tot_idx = tot_tree.set_index('nkey')[['actasContabilizadas','contabilizadas','totalActas','participacionCiudadana']].to_dict('index')
cbk = {}
for nk, g in cand_tree.groupby('nkey'):
    v = {}
    for _, r in g.iterrows():
        v[r['codigoAgrupacionPolitica']] = int(r['totalVotosValidos']) if pd.notna(r['totalVotosValidos']) else 0
    cbk[nk] = v

data = {}
for k in nodes:
    t = tot_idx.get(k, {})
    data[k] = {
        'av': round(float(t.get('actasContabilizadas',0) or 0), 3),
        'part': round(float(t.get('participacionCiudadana',0) or 0), 3),
        'cont': int(t.get('contabilizadas',0) or 0),
        'total': int(t.get('totalActas',0) or 0),
        'votes': cbk.get(k, {})
    }

out = {'snapshot':{'ts':snapshot_ts,'label':label,'avance':avance},
       'candidatos_master':candidatos_master, 'nodes':nodes, 'data':data}

ts_tag = dt_utc.strftime('%Y%m%dT%H%MZ')
name = f'onpe_data_{ts_tag}_{avance:.2f}pct.json'
path = os.path.join(OUT_DIR, name)
os.makedirs(OUT_DIR, exist_ok=True)

# check anti-duplicado: si ya existe un snapshot con ese nombre exacto, no hacer nada
if os.path.exists(path):
    print(f'SKIP: {name} ya existe — avance idéntico al último snapshot')
    sys.exit(0)

with open(path,'w',encoding='utf-8') as f:
    json.dump(out, f, ensure_ascii=False, separators=(',',':'))

n_ext = sum(1 for n in nodes.values() if n['ambito']==2)
print(f'archivo:  {name}')
print(f'tamaño:   {os.path.getsize(path)/1024:.1f} KB')
print(f'snapshot: {label} · avance {avance}%')
print(f'nodos:    {len(nodes)} ({n_ext} de extranjero)')
