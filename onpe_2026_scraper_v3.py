"""
ONPE 2026 — Scraper de resultados presidenciales (v3)
======================================================

Cambios vs v2:
    - Soporte para EXTRANJERO (idAmbitoGeografico=2)
    - Estructura jerárquica del extranjero: continentes → países → ciudades
      (mismos endpoints, distinto ámbito geográfico)
    - Por defecto trae ambos ámbitos. Flag --solo-peru / --solo-extranjero
      si quieres uno específico.
    - Cada fila lleva el campo `ambito` (1=peru, 2=extranjero) por si
      después quieres separar / sumar.
    - El nodo raíz "PERÚ" se mantiene para retrocompatibilidad pero
      ahora cuelgan también los continentes como "departamentos" extra.

Uso:
    python3 onpe_2026_scraper_v3.py --out ./onpe_out
    python3 onpe_2026_scraper_v3.py --out ./onpe_out --no-distrito
    python3 onpe_2026_scraper_v3.py --out ./onpe_out --solo-extranjero
"""

import argparse
import concurrent.futures as cf
import datetime as dt
import json
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


# ────────────────────────────────────────────────────────────────────
# Configuración
# ────────────────────────────────────────────────────────────────────

BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"
ID_ELECCION = 10
AMBITOS = {1: "peru", 2: "extranjero"}
MAX_WORKERS = 8
REQUEST_TIMEOUT = 30

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
    ),
    "Accept": "*/*",
    "Accept-Language": "es-ES,es;q=0.9",
    "Referer": "https://resultadoelectoral.onpe.gob.pe/main/presidenciales",
}


def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update(HEADERS)
    retry = Retry(
        total=5, backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
    )
    s.mount("https://", HTTPAdapter(max_retries=retry, pool_maxsize=MAX_WORKERS * 2))
    return s


SESSION = make_session()


def get_json(path: str, **params) -> Dict[str, Any]:
    r = SESSION.get(f"{BASE}{path}", params=params, timeout=REQUEST_TIMEOUT)
    r.raise_for_status()
    try:
        return r.json()
    except ValueError:
        raise RuntimeError(
            f"Respuesta no-JSON de {r.url}\n"
            f"HTTP {r.status_code} | Content-Type: {r.headers.get('content-type')}\n"
            f"Primeros 300 chars: {r.text[:300]!r}"
        )


# ────────────────────────────────────────────────────────────────────
# Listado de ubigeos
# ────────────────────────────────────────────────────────────────────

def fetch_departamentos(ambito: int) -> List[Dict]:
    return get_json("/ubigeos/departamentos",
                    idEleccion=ID_ELECCION, idAmbitoGeografico=ambito)["data"]


def fetch_provincias(ambito: int, ubigeo_dep: str) -> List[Dict]:
    try:
        return get_json("/ubigeos/provincias",
                        idEleccion=ID_ELECCION, idAmbitoGeografico=ambito,
                        idUbigeoDepartamento=ubigeo_dep)["data"]
    except requests.HTTPError:
        return []


def fetch_distritos(ambito: int, ubigeo_prov: str) -> List[Dict]:
    try:
        return get_json("/ubigeos/distritos",
                        idEleccion=ID_ELECCION, idAmbitoGeografico=ambito,
                        idUbigeoProvincia=ubigeo_prov)["data"]
    except requests.HTTPError:
        return []


# ────────────────────────────────────────────────────────────────────
# Captura por scope
# ────────────────────────────────────────────────────────────────────

def fetch_nacional() -> Dict:
    """Resumen nacional total (suma de Perú + Extranjero, según ONPE)."""
    scope = {"nivel": "nacional", "ubigeo": None, "nombre": "PERÚ",
             "ubigeo_dep": None, "ubigeo_prov": None,
             "nombre_dep": None, "nombre_prov": None,
             "ambito": 0}  # 0 = global
    totales = get_json("/resumen-general/totales",
                       idEleccion=ID_ELECCION, tipoFiltro="eleccion")
    cand = get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                    idEleccion=ID_ELECCION, tipoFiltro="eleccion")
    mesas = get_json("/mesa/totales", tipoFiltro="eleccion")
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
        "mesas": {**scope, **(mesas.get("data") or {})},
    }


def fetch_ambito_resumen(ambito: int) -> Dict:
    """Resumen agregado por ámbito (todo Perú o todo Extranjero)."""
    nombre = "PERUANOS RESIDENTES EN EL EXTRANJERO" if ambito == 2 else "PERÚ (TERRITORIO)"
    # ubigeo virtual: 800000 para perú-territorio, 900000 para extranjero
    ubigeo = "800000" if ambito == 1 else "900000"
    scope = {"nivel": "ambito", "ubigeo": ubigeo, "nombre": nombre,
             "ubigeo_dep": None, "ubigeo_prov": None,
             "nombre_dep": None, "nombre_prov": None,
             "ambito": ambito}
    tot = get_json("/resumen-general/totales",
                   idEleccion=ID_ELECCION, tipoFiltro="ambito_geografico",
                   idAmbitoGeografico=ambito)
    cand = get_json("/resumen-general/participantes",
                    idEleccion=ID_ELECCION, tipoFiltro="ambito_geografico",
                    idAmbitoGeografico=ambito)
    return {
        "totales": {**scope, **(tot.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


def fetch_departamento(ambito: int, ub_dep: str, nombre_dep: str) -> Dict:
    scope = {"nivel": "departamento", "ubigeo": ub_dep, "nombre": nombre_dep,
             "ubigeo_dep": ub_dep, "ubigeo_prov": None,
             "nombre_dep": nombre_dep, "nombre_prov": None,
             "ambito": ambito}
    tot = get_json("/resumen-general/totales",
                   idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                   tipoFiltro="ubigeo_nivel_01",
                   idUbigeoDepartamento=ub_dep)
    cand = get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                    tipoFiltro="ubigeo_nivel_01",
                    idAmbitoGeografico=ambito,
                    ubigeoNivel1=ub_dep,
                    listRegiones="TODOS,PERÚ,EXTRANJERO",
                    idEleccion=ID_ELECCION)
    return {
        "totales": {**scope, **(tot.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


def fetch_provincia(ambito: int, ub_dep: str, nombre_dep: str,
                    ub_prov: str, nombre_prov: str) -> Dict:
    scope = {"nivel": "provincia", "ubigeo": ub_prov, "nombre": nombre_prov,
             "ubigeo_dep": ub_dep, "ubigeo_prov": ub_prov,
             "nombre_dep": nombre_dep, "nombre_prov": None,
             "ambito": ambito}
    tot = get_json("/resumen-general/totales",
                   idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                   tipoFiltro="ubigeo_nivel_02",
                   idUbigeoDepartamento=ub_dep,
                   idUbigeoProvincia=ub_prov)
    cand = get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                    tipoFiltro="ubigeo_nivel_02",
                    idAmbitoGeografico=ambito,
                    ubigeoNivel1=ub_dep,
                    ubigeoNivel2=ub_prov,
                    listRegiones="TODOS,PERÚ,EXTRANJERO",
                    idEleccion=ID_ELECCION)
    return {
        "totales": {**scope, **(tot.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


def fetch_distrito(ambito: int, ub_dep: str, nombre_dep: str,
                   ub_prov: str, nombre_prov: str,
                   ub_dist: str, nombre_dist: str) -> Dict:
    scope = {"nivel": "distrito", "ubigeo": ub_dist, "nombre": nombre_dist,
             "ubigeo_dep": ub_dep, "ubigeo_prov": ub_prov,
             "nombre_dep": nombre_dep, "nombre_prov": nombre_prov,
             "ambito": ambito}
    tot = get_json("/resumen-general/totales",
                   idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                   tipoFiltro="ubigeo_nivel_03",
                   idUbigeoDepartamento=ub_dep,
                   idUbigeoProvincia=ub_prov,
                   idUbigeoDistrito=ub_dist)
    cand = get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                    tipoFiltro="ubigeo_nivel_03",
                    idAmbitoGeografico=ambito,
                    ubigeoNivel1=ub_dep,
                    ubigeoNivel2=ub_prov,
                    ubigeoNivel3=ub_dist,
                    idEleccion=ID_ELECCION)
    return {
        "totales": {**scope, **(tot.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


# ────────────────────────────────────────────────────────────────────
# Paralelización
# ────────────────────────────────────────────────────────────────────

def _safe(fn, *args) -> Tuple[str, Optional[Dict], Optional[str]]:
    key = str(args)
    try:
        return (key, fn(*args), None)
    except Exception as e:
        return (key, None, f"{type(e).__name__}: {e}")


def run_parallel(label: str, fn, arg_tuples: List[tuple]) -> List[Dict]:
    results, errors = [], []
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        futures = [ex.submit(_safe, fn, *args) for args in arg_tuples]
        for i, fut in enumerate(cf.as_completed(futures), 1):
            key, res, err = fut.result()
            if err:
                errors.append((key, err))
            else:
                results.append(res)
            if i % 50 == 0 or i == len(futures):
                print(f"  [{label}] {i}/{len(futures)}  (errores: {len(errors)})",
                      flush=True)
    if errors:
        print(f"  [{label}] muestra de errores:")
        for key, err in errors[:3]:
            print(f"    - {key[:80]}: {err[:120]}")
    return results


# ────────────────────────────────────────────────────────────────────
# Captura por ámbito
# ────────────────────────────────────────────────────────────────────

def capturar_ambito(ambito: int, incluir_distritos: bool = True) -> Dict:
    """Captura todo un ámbito (1=Perú, 2=Extranjero) en su jerarquía completa."""
    label_amb = AMBITOS[ambito]
    print(f"\n── Ámbito {ambito} ({label_amb}) ──")

    # Resumen del ámbito (suma de todo)
    print(f"  Resumen del ámbito...")
    ambito_summary = fetch_ambito_resumen(ambito)

    # Departamentos / continentes
    print(f"  Listando deptos...")
    deps = fetch_departamentos(ambito)
    print(f"    → {len(deps)} {'departamentos' if ambito==1 else 'continentes'}")
    dep_results = run_parallel(
        f"{label_amb}/dpto", fetch_departamento,
        [(ambito, d["ubigeo"], d["nombre"]) for d in deps]
    )

    # Provincias / países
    print(f"  Listando provincias...")
    provincias = []
    for d in deps:
        for p in fetch_provincias(ambito, d["ubigeo"]):
            provincias.append({
                "ubigeo_dep": d["ubigeo"], "nombre_dep": d["nombre"],
                "ubigeo": p["ubigeo"], "nombre": p["nombre"],
            })
    print(f"    → {len(provincias)} {'provincias' if ambito==1 else 'países'}")
    prov_results = run_parallel(
        f"{label_amb}/prov", fetch_provincia,
        [(ambito, p["ubigeo_dep"], p["nombre_dep"], p["ubigeo"], p["nombre"])
         for p in provincias]
    )

    # Distritos / ciudades
    dist_results = []
    distritos = []
    if incluir_distritos:
        print(f"  Listando distritos...")
        for p in provincias:
            for d in fetch_distritos(ambito, p["ubigeo"]):
                distritos.append({
                    "ubigeo_dep": p["ubigeo_dep"],
                    "nombre_dep": p["nombre_dep"],
                    "ubigeo_prov": p["ubigeo"],
                    "nombre_prov": p["nombre"],
                    "ubigeo": d["ubigeo"], "nombre": d["nombre"],
                })
        print(f"    → {len(distritos)} {'distritos' if ambito==1 else 'ciudades'}")
        if distritos:
            dist_results = run_parallel(
                f"{label_amb}/dist", fetch_distrito,
                [(ambito, d["ubigeo_dep"], d["nombre_dep"],
                  d["ubigeo_prov"], d["nombre_prov"],
                  d["ubigeo"], d["nombre"]) for d in distritos]
            )

    return {
        "ambito_summary": ambito_summary,
        "deps": dep_results,
        "provs": prov_results,
        "dists": dist_results,
        "n_deps": len(dep_results),
        "n_provs": len(prov_results),
        "n_dists": len(dist_results),
    }


# ────────────────────────────────────────────────────────────────────
# Snapshot
# ────────────────────────────────────────────────────────────────────

def run_snapshot(out_dir: pathlib.Path,
                 incluir_distritos: bool = True,
                 ambitos_run: List[int] = [1, 2]) -> Dict:
    ts = dt.datetime.now(dt.timezone.utc)
    snap_dir = out_dir / f"snapshot_{ts.strftime('%Y%m%dT%H%M%SZ')}"
    snap_dir.mkdir(parents=True, exist_ok=True)
    print(f"\n▶ Snapshot ONPE — {ts.isoformat()}")
    print(f"  Carpeta: {snap_dir}")
    print(f"  Ámbitos: {[AMBITOS[a] for a in ambitos_run]}\n")

    # 1. Nacional global (suma de todo)
    print("[1/N] Nacional global (eleccion)...")
    nac = fetch_nacional()

    # 2. Capturas por ámbito
    capturas = {}
    for amb in ambitos_run:
        capturas[amb] = capturar_ambito(amb, incluir_distritos=incluir_distritos)

    # ─ Consolidación
    totales_rows = [nac["totales"]]
    cand_rows = list(nac["candidatos"])

    for amb, cap in capturas.items():
        totales_rows.append(cap["ambito_summary"]["totales"])
        cand_rows.extend(cap["ambito_summary"]["candidatos"])
        for lvl in (cap["deps"], cap["provs"], cap["dists"]):
            for r in lvl:
                totales_rows.append(r["totales"])
                cand_rows.extend(r["candidatos"])

    df_tot = pd.DataFrame(totales_rows)
    df_cand = pd.DataFrame(cand_rows)
    df_tot["snapshot_utc"] = ts.isoformat()
    df_cand["snapshot_utc"] = ts.isoformat()

    # ─ Persistencia
    df_tot.to_csv(snap_dir / "totales.csv", index=False)
    df_tot.to_parquet(snap_dir / "totales.parquet", index=False)
    df_cand.to_csv(snap_dir / "candidatos.csv", index=False)
    df_cand.to_parquet(snap_dir / "candidatos.parquet", index=False)

    with open(snap_dir / "raw_nacional.json", "w", encoding="utf-8") as f:
        json.dump(nac, f, ensure_ascii=False, indent=2, default=str)

    summary = {
        "snapshot_utc": ts.isoformat(),
        "rows_totales": len(df_tot),
        "rows_candidatos": len(df_cand),
        "ambitos": [AMBITOS[a] for a in ambitos_run],
        "n_departamentos_peru": capturas.get(1, {}).get("n_deps", 0),
        "n_provincias_peru": capturas.get(1, {}).get("n_provs", 0),
        "n_distritos_peru": capturas.get(1, {}).get("n_dists", 0),
        "n_continentes_extranjero": capturas.get(2, {}).get("n_deps", 0),
        "n_paises_extranjero": capturas.get(2, {}).get("n_provs", 0),
        "n_ciudades_extranjero": capturas.get(2, {}).get("n_dists", 0),
        "avance_nacional_pct": nac["totales"].get("actasContabilizadas"),
        "actas_contabilizadas": nac["totales"].get("contabilizadas"),
        "total_actas": nac["totales"].get("totalActas"),
        "out_dir": str(snap_dir),
    }
    with open(snap_dir / "summary.json", "w", encoding="utf-8") as f:
        json.dump(summary, f, ensure_ascii=False, indent=2, default=str)

    print(f"\n✓ Snapshot completo")
    print(f"  Totales:    {summary['rows_totales']} filas")
    print(f"  Candidatos: {summary['rows_candidatos']} filas")
    if 1 in ambitos_run:
        print(f"  PERÚ:       {summary['n_departamentos_peru']} dpto · "
              f"{summary['n_provincias_peru']} prov · {summary['n_distritos_peru']} dist")
    if 2 in ambitos_run:
        print(f"  EXTRANJERO: {summary['n_continentes_extranjero']} continentes · "
              f"{summary['n_paises_extranjero']} países · {summary['n_ciudades_extranjero']} ciudades")
    print(f"  Avance nacional: {summary['avance_nacional_pct']}% "
          f"({summary['actas_contabilizadas']}/{summary['total_actas']} actas)")
    return summary


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="ONPE 2026 presidencial — snapshot (v3, con extranjero)")
    ap.add_argument("--out", default="./onpe_out")
    ap.add_argument("--no-distrito", action="store_true",
                    help="Omitir nivel distrito/ciudad (más rápido)")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--solo-peru", action="store_true",
                     help="Solo capturar ámbito Perú")
    grp.add_argument("--solo-extranjero", action="store_true",
                     help="Solo capturar ámbito Extranjero")
    args = ap.parse_args()

    if args.solo_peru:
        ambitos = [1]
    elif args.solo_extranjero:
        ambitos = [2]
    else:
        ambitos = [1, 2]

    out = pathlib.Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    start = time.time()
    run_snapshot(out,
                 incluir_distritos=not args.no_distrito,
                 ambitos_run=ambitos)
    print(f"\n⏱  {time.time()-start:.1f}s total")
