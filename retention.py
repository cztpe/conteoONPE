"""
Política de retención de snapshots ONPE.

Reglas:
  - Snapshots < 24h: se conservan TODOS (cada 30 min)
  - Snapshots ≥ 24h: se conserva solo 1 por hora (el más reciente de cada hora)
  - Siempre se conserva el más antiguo (baseline) y el más reciente

Uso:
    python retention.py <DIR>

Donde DIR es la carpeta que contiene los archivos onpe_data_<timestamp>_<avance>pct.json
"""
import sys, os, re
from datetime import datetime, timedelta, timezone

PATTERN = re.compile(r'^onpe_data_(\d{8}T\d{4}Z)_[\d.]+pct\.json$')

def parse_ts(fname):
    m = PATTERN.match(fname)
    if not m: return None
    # 20260414T0519Z → 2026-04-14T05:19Z
    s = m.group(1)
    return datetime.strptime(s, '%Y%m%dT%H%MZ').replace(tzinfo=timezone.utc)

def main():
    target_dir = sys.argv[1] if len(sys.argv) > 1 else '.'
    files = []
    for fname in os.listdir(target_dir):
        ts = parse_ts(fname)
        if ts:
            files.append((ts, fname))
    files.sort(key=lambda x: x[0])  # cronológico ascendente

    if len(files) <= 2:
        print(f'retention: solo {len(files)} archivo(s), nada que limpiar')
        return

    now = datetime.now(timezone.utc)
    cutoff_recent = now - timedelta(hours=24)

    oldest = files[0]    # baseline (siempre se queda)
    newest = files[-1]   # más reciente (siempre se queda)

    keep = {oldest[1], newest[1]}

    # recientes (< 24h): conservar todos
    # antiguos (≥ 24h): agrupar por hora, conservar solo el último de cada hora
    by_hour = {}  # clave: (YYYY,MM,DD,HH) → (ts, fname) más reciente
    for ts, fname in files:
        if ts >= cutoff_recent:
            keep.add(fname)
        else:
            hour_key = (ts.year, ts.month, ts.day, ts.hour)
            if hour_key not in by_hour or ts > by_hour[hour_key][0]:
                by_hour[hour_key] = (ts, fname)

    for ts, fname in by_hour.values():
        keep.add(fname)

    # borrar lo que no está en keep
    removed = 0
    kept = 0
    for ts, fname in files:
        if fname in keep:
            kept += 1
        else:
            path = os.path.join(target_dir, fname)
            try:
                os.remove(path)
                removed += 1
                print(f'  borrado: {fname}')
            except OSError as e:
                print(f'  error al borrar {fname}: {e}', file=sys.stderr)

    print(f'retention: {kept} conservado(s), {removed} borrado(s)')

if __name__ == '__main__':
    main()
