"""
ONPE 2026 — Scraper de resultados presidenciales (stealth)
==========================================================

Versión con camuflaje de headers y warm-up para pasar WAFs desde
entornos de datacenter (GitHub Actions / Azure / AWS).

Cambios vs versión base:
    - User-Agent de Linux (coincide con la plataforma del runner)
    - Set completo de Client Hints (sec-ch-ua*)
    - Sec-Fetch-* correctos (navegación entre páginas del mismo origen)
    - Origin header
    - Warm-up: primero un GET a la home para obtener cookies de sesión
    - Cookies persistentes en el cliente httpx
    - User-Agent consistente con sec-ch-ua

Uso:
    pip install 'httpx[http2]' pandas pyarrow
    python3 onpe_scraper.py --out ./onpe_out
    python3 onpe_scraper.py --out ./onpe_out --no-distrito
"""

import argparse
import asyncio
import datetime as dt
import json
import pathlib
import time
from typing import Any, Dict, List, Optional, Tuple

import httpx
import pandas as pd


# ────────────────────────────────────────────────────────────────────
# Configuración
# ────────────────────────────────────────────────────────────────────

BASE = "https://resultadoelectoral.onpe.gob.pe/presentacion-backend"
HOME_URL = "https://resultadoelectoral.onpe.gob.pe/main/presidenciales"
ROOT_URL = "https://resultadoelectoral.onpe.gob.pe/"
ID_ELECCION = 10
AMBITOS = {1: "peru", 2: "extranjero"}
CONCURRENCY = 20
REQUEST_TIMEOUT = 20
MAX_RETRIES = 3
RETRY_BACKOFF = 0.5

# Headers que imitan Chrome 131 en Linux (coincide con runners GH Actions)
# Los sec-ch-ua deben ser consistentes con el User-Agent.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/131.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "es-ES,es;q=0.9,en;q=0.8",
    "Accept-Encoding": "gzip, deflate, br, zstd",
    "sec-ch-ua": '"Google Chrome";v="131", "Chromium";v="131", "Not_A Brand";v="24"',
    "sec-ch-ua-mobile": "?0",
    "sec-ch-ua-platform": '"Linux"',
    "Sec-Fetch-Dest": "empty",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Site": "same-origin",
    "Referer": HOME_URL,
    "Origin": "https://resultadoelectoral.onpe.gob.pe",
    "DNT": "1",
    "Connection": "keep-alive",
    "Pragma": "no-cache",
    "Cache-Control": "no-cache",
}

# Headers especiales para el warm-up (navegación real)
WARMUP_HEADERS = {
    "User-Agent": HEADERS["User-Agent"],
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
    "Accept-Language": HEADERS["Accept-Language"],
    "Accept-Encoding": HEADERS["Accept-Encoding"],
    "sec-ch-ua": HEADERS["sec-ch-ua"],
    "sec-ch-ua-mobile": HEADERS["sec-ch-ua-mobile"],
    "sec-ch-ua-platform": HEADERS["sec-ch-ua-platform"],
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Sec-Fetch-User": "?1",
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
    "Connection": "keep-alive",
}


# ────────────────────────────────────────────────────────────────────
# Cliente async global + semáforo de concurrencia
# ────────────────────────────────────────────────────────────────────

CLIENT: Optional[httpx.AsyncClient] = None
SEM: Optional[asyncio.Semaphore] = None


async def warm_up() -> None:
    """Primera visita al sitio para obtener cookies y pasar el WAF."""
    print("  [warm-up] visitando home de ONPE...")
    try:
        # 1. root
        r1 = await CLIENT.get(ROOT_URL, headers=WARMUP_HEADERS,
                              timeout=REQUEST_TIMEOUT, follow_redirects=True)
        print(f"    root: HTTP {r1.status_code} ({len(r1.content)} bytes, "
              f"cookies: {len(r1.cookies)})")
        # 2. home de presidenciales (la que el usuario vería)
        r2 = await CLIENT.get(HOME_URL, headers=WARMUP_HEADERS,
                              timeout=REQUEST_TIMEOUT, follow_redirects=True)
        print(f"    presidenciales: HTTP {r2.status_code} ({len(r2.content)} bytes, "
              f"cookies: {len(r2.cookies)})")
        # breve pausa simulando que la página carga
        await asyncio.sleep(0.5)
    except Exception as e:
        print(f"    ⚠ warm-up falló: {e}")


async def get_json(path: str, **params) -> Dict[str, Any]:
    """GET con reintentos y cap de concurrencia."""
    async with SEM:
        last_err = None
        for attempt in range(MAX_RETRIES):
            try:
                r = await CLIENT.get(f"{BASE}{path}", params=params,
                                     timeout=REQUEST_TIMEOUT)
                r.raise_for_status()
                ctype = r.headers.get("content-type", "")
                if "json" not in ctype.lower():
                    raise RuntimeError(
                        f"respuesta no-JSON: {r.url}\n"
                        f"HTTP {r.status_code} | content-type: {ctype}\n"
                        f"primeros 200 chars: {r.text[:200]!r}"
                    )
                return r.json()
            except (httpx.TimeoutException, httpx.ConnectError,
                    httpx.RemoteProtocolError, httpx.ReadError) as e:
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1))
            except RuntimeError as e:
                # si fue respuesta no-JSON, no reintentar inmediato (WAF)
                last_err = e
                if attempt < MAX_RETRIES - 1:
                    await asyncio.sleep(RETRY_BACKOFF * (attempt + 1) * 2)
                    # reintentar warm-up por si las cookies expiraron
                    if attempt == 0:
                        await warm_up()
        raise last_err  # type: ignore


# ────────────────────────────────────────────────────────────────────
# Listados de ubigeos
# ────────────────────────────────────────────────────────────────────

async def fetch_departamentos(ambito: int) -> List[Dict]:
    r = await get_json("/ubigeos/departamentos",
                       idEleccion=ID_ELECCION, idAmbitoGeografico=ambito)
    return r["data"]


async def fetch_provincias(ambito: int, ubigeo_dep: str) -> List[Dict]:
    try:
        r = await get_json("/ubigeos/provincias",
                           idEleccion=ID_ELECCION, idAmbitoGeografico=ambito,
                           idUbigeoDepartamento=ubigeo_dep)
        return r["data"]
    except (httpx.HTTPStatusError, RuntimeError):
        return []


async def fetch_distritos(ambito: int, ubigeo_prov: str) -> List[Dict]:
    try:
        r = await get_json("/ubigeos/distritos",
                           idEleccion=ID_ELECCION, idAmbitoGeografico=ambito,
                           idUbigeoProvincia=ubigeo_prov)
        return r["data"]
    except (httpx.HTTPStatusError, RuntimeError):
        return []


# ────────────────────────────────────────────────────────────────────
# Captura por scope
# ────────────────────────────────────────────────────────────────────

async def fetch_nacional() -> Dict:
    scope = {"nivel": "nacional", "ubigeo": None, "nombre": "PERÚ",
             "ubigeo_dep": None, "ubigeo_prov": None,
             "nombre_dep": None, "nombre_prov": None, "ambito": 0}
    totales, cand, mesas = await asyncio.gather(
        get_json("/resumen-general/totales",
                 idEleccion=ID_ELECCION, tipoFiltro="eleccion"),
        get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                 idEleccion=ID_ELECCION, tipoFiltro="eleccion"),
        get_json("/mesa/totales", tipoFiltro="eleccion"),
    )
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
        "mesas": {**scope, **(mesas.get("data") or {})},
    }


async def fetch_ambito_resumen(ambito: int) -> Dict:
    nombre = ("PERUANOS RESIDENTES EN EL EXTRANJERO" if ambito == 2
              else "PERÚ (TERRITORIO)")
    ubigeo_virtual = "800000" if ambito == 1 else "900000"
    scope = {"nivel": "ambito", "ubigeo": ubigeo_virtual, "nombre": nombre,
             "ubigeo_dep": None, "ubigeo_prov": None,
             "nombre_dep": None, "nombre_prov": None, "ambito": ambito}
    totales, cand = await asyncio.gather(
        get_json("/resumen-general/totales",
                 idEleccion=ID_ELECCION, tipoFiltro="ambito_geografico",
                 idAmbitoGeografico=ambito),
        get_json("/resumen-general/participantes",
                 idEleccion=ID_ELECCION, tipoFiltro="ambito_geografico",
                 idAmbitoGeografico=ambito),
    )
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


async def fetch_departamento(ambito: int, ub_dep: str, nombre_dep: str) -> Dict:
    scope = {"nivel": "departamento", "ubigeo": ub_dep, "nombre": nombre_dep,
             "ubigeo_dep": ub_dep, "ubigeo_prov": None,
             "nombre_dep": nombre_dep, "nombre_prov": None, "ambito": ambito}
    totales, cand = await asyncio.gather(
        get_json("/resumen-general/totales",
                 idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                 tipoFiltro="ubigeo_nivel_01", idUbigeoDepartamento=ub_dep),
        get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                 tipoFiltro="ubigeo_nivel_01", idAmbitoGeografico=ambito,
                 ubigeoNivel1=ub_dep, listRegiones="TODOS,PERÚ,EXTRANJERO",
                 idEleccion=ID_ELECCION),
    )
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


async def fetch_provincia(ambito: int, ub_dep: str, nombre_dep: str,
                          ub_prov: str, nombre_prov: str) -> Dict:
    scope = {"nivel": "provincia", "ubigeo": ub_prov, "nombre": nombre_prov,
             "ubigeo_dep": ub_dep, "ubigeo_prov": ub_prov,
             "nombre_dep": nombre_dep, "nombre_prov": None, "ambito": ambito}
    totales, cand = await asyncio.gather(
        get_json("/resumen-general/totales",
                 idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                 tipoFiltro="ubigeo_nivel_02",
                 idUbigeoDepartamento=ub_dep, idUbigeoProvincia=ub_prov),
        get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                 tipoFiltro="ubigeo_nivel_02", idAmbitoGeografico=ambito,
                 ubigeoNivel1=ub_dep, ubigeoNivel2=ub_prov,
                 listRegiones="TODOS,PERÚ,EXTRANJERO", idEleccion=ID_ELECCION),
    )
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


async def fetch_distrito(ambito: int, ub_dep: str, nombre_dep: str,
                         ub_prov: str, nombre_prov: str,
                         ub_dist: str, nombre_dist: str) -> Dict:
    scope = {"nivel": "distrito", "ubigeo": ub_dist, "nombre": nombre_dist,
             "ubigeo_dep": ub_dep, "ubigeo_prov": ub_prov,
             "nombre_dep": nombre_dep, "nombre_prov": nombre_prov,
             "ambito": ambito}
    totales, cand = await asyncio.gather(
        get_json("/resumen-general/totales",
                 idAmbitoGeografico=ambito, idEleccion=ID_ELECCION,
                 tipoFiltro="ubigeo_nivel_03",
                 idUbigeoDepartamento=ub_dep, idUbigeoProvincia=ub_prov,
                 idUbigeoDistrito=ub_dist),
        get_json("/eleccion-presidencial/participantes-ubicacion-geografica-nombre",
                 tipoFiltro="ubigeo_nivel_03", idAmbitoGeografico=ambito,
                 ubigeoNivel1=ub_dep, ubigeoNivel2=ub_prov,
                 ubigeoNivel3=ub_dist, idEleccion=ID_ELECCION),
    )
    return {
        "totales": {**scope, **(totales.get("data") or {})},
        "candidatos": [{**scope, **c} for c in (cand.get("data") or [])],
    }


# ────────────────────────────────────────────────────────────────────
# Paralelización
# ────────────────────────────────────────────────────────────────────

async def _safe(coro_fn, *args) -> Tuple[str, Optional[Dict], Optional[str]]:
    key = str(args)
    try:
        return (key, await coro_fn(*args), None)
    except Exception as e:
        return (key, None, f"{type(e).__name__}: {str(e)[:100]}")


async def run_parallel(label: str, coro_fn, arg_tuples: List[tuple]) -> List[Dict]:
    results, errors = [], []
    tasks = [asyncio.create_task(_safe(coro_fn, *args)) for args in arg_tuples]
    for i, fut in enumerate(asyncio.as_completed(tasks), 1):
        key, res, err = await fut
        if err:
            errors.append((key, err))
        else:
            results.append(res)
        if i % 50 == 0 or i == len(tasks):
            print(f"  [{label}] {i}/{len(tasks)}  (errores: {len(errors)})",
                  flush=True)
    if errors:
        print(f"  [{label}] muestra de errores:")
        for key, err in errors[:3]:
            print(f"    - {key[:80]}: {err[:120]}")
    return results


# ────────────────────────────────────────────────────────────────────
# Captura por ámbito
# ────────────────────────────────────────────────────────────────────

async def capturar_ambito(ambito: int, incluir_distritos: bool = True) -> Dict:
    label_amb = AMBITOS[ambito]
    print(f"\n── Ámbito {ambito} ({label_amb}) ──")

    print(f"  Resumen del ámbito...")
    ambito_summary = await fetch_ambito_resumen(ambito)

    print(f"  Listando deptos...")
    deps = await fetch_departamentos(ambito)
    print(f"    → {len(deps)} {'departamentos' if ambito==1 else 'continentes'}")
    dep_results = await run_parallel(
        f"{label_amb}/dpto", fetch_departamento,
        [(ambito, d["ubigeo"], d["nombre"]) for d in deps]
    )

    print(f"  Listando provincias...")
    provs_raw = await asyncio.gather(
        *[fetch_provincias(ambito, d["ubigeo"]) for d in deps]
    )
    provincias = []
    for d, ps in zip(deps, provs_raw):
        for p in ps:
            provincias.append({
                "ubigeo_dep": d["ubigeo"], "nombre_dep": d["nombre"],
                "ubigeo": p["ubigeo"], "nombre": p["nombre"],
            })
    print(f"    → {len(provincias)} {'provincias' if ambito==1 else 'países'}")
    prov_results = await run_parallel(
        f"{label_amb}/prov", fetch_provincia,
        [(ambito, p["ubigeo_dep"], p["nombre_dep"], p["ubigeo"], p["nombre"])
         for p in provincias]
    )

    dist_results = []
    if incluir_distritos:
        print(f"  Listando distritos...")
        dists_raw = await asyncio.gather(
            *[fetch_distritos(ambito, p["ubigeo"]) for p in provincias]
        )
        distritos = []
        for p, ds in zip(provincias, dists_raw):
            for d in ds:
                distritos.append({
                    "ubigeo_dep": p["ubigeo_dep"], "nombre_dep": p["nombre_dep"],
                    "ubigeo_prov": p["ubigeo"], "nombre_prov": p["nombre"],
                    "ubigeo": d["ubigeo"], "nombre": d["nombre"],
                })
        print(f"    → {len(distritos)} {'distritos' if ambito==1 else 'ciudades'}")
        if distritos:
            dist_results = await run_parallel(
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

async def run_snapshot(out_dir: pathlib.Path,
                       incluir_distritos: bool = True,
                       ambitos_run: List[int] = [1, 2]) -> Dict:
    global CLIENT, SEM
    limits = httpx.Limits(max_connections=CONCURRENCY * 2,
                          max_keepalive_connections=CONCURRENCY)
    # jar de cookies persistente
    jar = httpx.Cookies()
    CLIENT = httpx.AsyncClient(
        http2=True,
        headers=HEADERS,
        limits=limits,
        timeout=REQUEST_TIMEOUT,
        follow_redirects=True,
        cookies=jar,
    )
    SEM = asyncio.Semaphore(CONCURRENCY)

    try:
        ts = dt.datetime.now(dt.timezone.utc)
        snap_dir = out_dir / f"snapshot_{ts.strftime('%Y%m%dT%H%M%SZ')}"
        snap_dir.mkdir(parents=True, exist_ok=True)

        print(f"\n▶ Snapshot ONPE — {ts.isoformat()}")
        print(f"  Carpeta: {snap_dir}")
        print(f"  Ámbitos: {[AMBITOS[a] for a in ambitos_run]}")
        print(f"  Motor: httpx async http/2, concurrency={CONCURRENCY}")

        # WARM-UP: visita a la home para obtener cookies de sesión
        await warm_up()

        print("\n[1/N] Nacional global...")
        nac = await fetch_nacional()

        capturas = {}
        for amb in ambitos_run:
            capturas[amb] = await capturar_ambito(amb, incluir_distritos)

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

        df_tot.to_csv(snap_dir / "totales.csv", index=False)
        df_cand.to_csv(snap_dir / "candidatos.csv", index=False)
        # parquet opcional: normaliza columna codigoAgrupacionPolitica a string
        # para evitar error de tipo mixto en pyarrow
        try:
            df_tot_p = df_tot.copy()
            df_cand_p = df_cand.copy()
            if 'codigoAgrupacionPolitica' in df_cand_p.columns:
                df_cand_p['codigoAgrupacionPolitica'] = df_cand_p['codigoAgrupacionPolitica'].astype(str)
            df_tot_p.to_parquet(snap_dir / "totales.parquet", index=False)
            df_cand_p.to_parquet(snap_dir / "candidatos.parquet", index=False)
        except Exception as e:
            print(f"  ⚠ parquet falló (no crítico): {e}")

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

    finally:
        await CLIENT.aclose()


# ────────────────────────────────────────────────────────────────────
# CLI
# ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    ap = argparse.ArgumentParser(
        description="ONPE 2026 presidencial — snapshot (stealth)")
    ap.add_argument("--out", default="./onpe_out")
    ap.add_argument("--no-distrito", action="store_true")
    grp = ap.add_mutually_exclusive_group()
    grp.add_argument("--solo-peru", action="store_true")
    grp.add_argument("--solo-extranjero", action="store_true")
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
    asyncio.run(run_snapshot(out,
                             incluir_distritos=not args.no_distrito,
                             ambitos_run=ambitos))
    print(f"\n⏱  {time.time()-start:.1f}s total")
