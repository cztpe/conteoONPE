# conteoONPE

monitor ciudadano del conteo de votos ONPE · elecciones generales perú 2026 · primera vuelta

dashboard standalone (single-file HTML, ~3200 líneas) que consume snapshots JSON generados a partir del scraping del portal de resultados electorales de ONPE. desplegado en github pages: `cztpe.github.io/conteoONPE/`

---

## estructura del repo

```
conteoONPE/
├── index.html                    ← dashboard principal (SPA, standalone, ~3200 líneas)
├── castillo_2021.json            ← baseline: votos de Castillo primera vuelta 2021 (P16)
├── participacion_baseline.json   ← baseline: participación ciudadana 2018 por depto + 40% extranjero
├── snapshots/                    ← subcarpeta con todos los snapshots
│   ├── onpe_data_20260414T0306Z_66.83pct.json
│   ├── onpe_data_20260414T0415Z_68.40pct.json
│   ├── ...
│   └── onpe_data_20260415T2357Z_91.72pct.json
└── README.md
```

los archivos `castillo_2021.json` y `participacion_baseline.json` van en la **raíz** del repo (no en `snapshots/`). el dashboard los busca con `fetch('./castillo_2021.json')` y `fetch('./participacion_baseline.json')`.

---

## flujo de trabajo

### 1. scraping (manual, en mac local)

el script `onpe_scraper.py` (NO está en el repo, solo en las máquinas locales) hace scraping del portal de resultados de ONPE via `httpx` async http/2. genera dos CSVs por corrida:

- `candidatos.csv` — votos por candidatx en cada nivel territorial
- `totales.csv` — avance de actas, participación, totales por nivel territorial

ambos CSVs incluyen perú (25 deptos, 196 provs, ~1879 distritos) y extranjero (5 continentes, 77 países, 210 ciudades).

#### máquinas configuradas

| máquina | ruta del scraper | ruta de salida |
|---------|-----------------|----------------|
| mac de melissa | `/Users/czt/Desktop/Votos/onpe_scraper.py` | `./onpe_out/` |
| mac de carlos (apple silicon) | `/Users/carloszevallos/Desktop/ONPE/onpe_scraper.py` | `./onpe_out/` |

en la mac de carlos se usa miniforge standalone (`./pyenv/bin/python3`).

#### ejecución

```bash
# mac de melissa (python3 del sistema)
cd /Users/czt/Desktop/Votos
python3 onpe_scraper.py --out ./onpe_out

# mac de carlos (miniforge local)
cd /Users/carloszevallos/Desktop/ONPE
./pyenv/bin/python3 onpe_scraper.py --out ./onpe_out
```

### 2. build_data.py (se corre en Claude)

transforma los dos CSVs en un JSON optimizado para el dashboard. se ejecuta dentro de la conversación con Claude:

- input: `candidatos.csv` + `totales.csv` subidos como attachments
- output: `onpe_data_{timestamp}_{avance}pct.json` en `/mnt/user-data/outputs/`

el script vive en `/home/claude/build_data.py` y se ejecuta con `python3 build_data.py`. lee los CSVs de `/mnt/user-data/uploads/` y escribe el JSON a `/mnt/user-data/outputs/`.

### 3. deploy

subir manualmente el JSON generado a `snapshots/` en el repo github. el dashboard autodescubre los snapshots via github API.

---

## formato de datos

### snapshot JSON

cada archivo `onpe_data_*.json` contiene:

```json
{
  "snapshot": {
    "ts": "2026-04-15T23:57:48+00:00",  // timestamp UTC del scraping
    "label": "15 apr 18:57",              // hora lima legible
    "avance": 91.719                      // % de actas contabilizadas a nivel nacional
  },
  "candidatos_master": [
    {"code": "8", "agr": "FUERZA POPULAR", "cand": "KEIKO SOFIA FUJIMORI HIGUCHI"},
    {"code": "35", "agr": "RENOVACIÓN POPULAR", "cand": "RAFAEL BERNARDO LÓPEZ ALIAGA CAZORLA"},
    // ... 38 entries total (incluye VOTOS EN BLANCO y VOTOS NULOS)
  ],
  "nodes": {
    "__root__": {"nombre": "PERÚ", "nivel": "nacional", "parent": null, "children": ["010000", ..., "900000"]},
    "140000": {"nombre": "LIMA", "nivel": "departamento", "parent": "__root__", "ubigeo": "140000", "ambito": 1, "children": [...]},
    "900000": {"nombre": "PERUANOS RESIDENTES EN EL EXTRANJERO", "nivel": "ambito", "parent": "__root__", "ambito": 2, "children": ["910000", "920000", "930000", "940000", "950000"]},
    "920000": {"nombre": "AMÉRICA", "nivel": "departamento", "parent": "900000", "ambito": 2, "children": [...]},
    "920200": {"nombre": "ARGENTINA", "nivel": "provincia", "parent": "920000", "ambito": 2, "children": ["920202", ...]},
    "920202": {"nombre": "BUENOS AIRES", "nivel": "distrito", "parent": "920200", "ambito": 2, "children": []},
    // ~2407 nodos total (2114 perú + 293 extranjero)
  },
  "data": {
    "__root__": {"av": 91.719, "part": 68.013, "cont": 85040, "total": 92766, "votes": {"8": 2641556, "35": 1836964, ...}},
    "140000": {"av": 93.192, "cont": 27256, "total": 29247, "votes": {...}},
    // un entry por cada nodo en nodes
  }
}
```

#### jerarquía territorial

```
__root__ (nacional)
├── deptos peruanos (nivel: departamento, ambito: 1)
│   ├── provincias (nivel: provincia)
│   │   └── distritos (nivel: distrito)
├── 900000 EXTRANJERO (nivel: ambito, ambito: 2)
│   ├── continentes (nivel: departamento, ambito: 2)
│   │   ├── países (nivel: provincia)
│   │   │   └── ciudades (nivel: distrito)
```

los códigos de candidatx en `votes` corresponden al campo `code` de `candidatos_master`. los votos son absolutos (no porcentajes).

### castillo_2021.json

baseline de primera vuelta 2021, Pedro Castillo (columna P16 en los CSVs originales de ONPE 2021). ~193 KB. estructura:

```json
{
  "meta": {"total_nacional": {"votos": 2837933, "pct": 18.92}},
  "depto": {"010000": {"pct": 14.52, "votos": 28123}, ...},
  "provincia": {"010100": {"pct": 11.23, "votos": 5891}, ...},
  "distrito": {"010101": {"pct": 9.87, "votos": 1234}, ...}
}
```

### participacion_baseline.json

participación ciudadana 2018 por departamento. 781 bytes.

```json
{
  "meta": {
    "fuente": "ONPE 2018 Elecciones Regionales y Municipales",
    "extranjero_default_pct": 40.0
  },
  "depto_pct": {
    "010000": 75.29,  // AMAZONAS
    "020000": 79.07,  // ANCASH
    "040000": 83.41,  // AREQUIPA
    "140000": 82.61,  // LIMA
    // ... 25 departamentos
  }
}
```

---

## arquitectura del dashboard (index.html)

standalone SPA. cero dependencias externas. ~3200 líneas de HTML + CSS + JS en un solo archivo.

### autodescubrimiento de snapshots

el dashboard busca archivos `onpe_data_*.json` de tres maneras (en orden de prioridad):

1. **manifest.json** — `./snapshots/manifest.json` con un array de nombres de archivo
2. **github API** — lista el contenido de `./snapshots/` via `api.github.com/repos/{owner}/{repo}/contents/snapshots/`
3. **fallback** — error con instrucciones

la subcarpeta se configura con el param `?subdir=` (default: `snapshots`). para volver a la raíz: `?subdir=`.

### estado (localStorage)

key: `onpe_charts_v1`. persiste:

- `places` — array de keys de nodos en el lienzo de gráficos
- `yMode` — `pct` | `votes`
- `xMode` — `snap` | `avance`
- `cast` — array de codes de candidatxs activos
- `snapsInactive` — set de timestamps de snapshots ocultos
- `duelos` — array de `{a, b, place}` (duelos configurados)
- `showCastillo` — boolean (toggle de línea Castillo 2021)
- `csSort` — `{col, dir}` para tabla comparativa
- `mapMode` — `winner` | `avance` | `margin` | `part` | `candidato`
- `mapCand` — code del candidatx para modo candidato del mapa
- `density` — `wide` | `normal` | `compact`

### secciones del dashboard

1. **barra de snapshots** — chips clickeables para activar/desactivar snapshots individuales
2. **movimientos** — panel con 4 subsecciones:
   - resumen (avance Δ, votos nuevos)
   - tabla de cambio de % por candidatx (headers = avance del snapshot, no número de snapshot)
   - origen de votos nuevos (top 8 territorios con más votos nuevos entre los últimos 2 snapshots activos)
   - eventos notables (flips departamentales, cambios de puesto, cambios de margen, saltos de avance)
3. **informe nacional** — tarjetas: líder, margen, quién sube/baja más, puesto más cerrado, deptos que lidera el 1°, heredero del voto castillo, herencia en strongholds
4. **leyenda + picker de candidatxs** — chips con colores asignados, click para agregar/quitar del cast (máx 12)
5. **controles** — buscador de lugares, modo Y (% / votos), modo X (snapshots / avance), densidad (amplio/normal/compacto), toggle Castillo 2021, limpiar lienzo
6. **lienzo de gráficos (grid)** — gráficos SVG por lugar. cada gráfico muestra líneas por candidatx del cast. modos de eje X: snapshots o avance continuo (0→100%). incluye línea punteada roja de Castillo 2021 (si toggle activo)
7. **playback timeline** — debajo del lienzo. controles: play/pause, step back/fwd, slider, acumulativo vs single, velocidad (lento/normal/rápido)
8. **duelos** — comparación directa entre dos candidatxs en un lugar. header muestra liderazgo en votos Y en pp simultáneamente. tendencia detecta flips ("flip: Roberto tomó la delantera, giro de +383k")
9. **navegador territorial (browser)** — chips por depto + nodo extranjero. click para agregar al lienzo. drill genérico: cualquier nodo con hijos es drillable. flujo: deptos → provs → dists (perú) / extranjero → continentes → países → ciudades. headers dinámicos según contexto
10. **tabla comparativa castillo vs sánchez** — sortable por 5 columnas (territorio, c21%, sp%, delta, avance). filas verdes = Sánchez ≥ Castillo. click en fila agrega el depto al lienzo
11. **mapa de grilla** — SVG topológico 10×7. 25 deptos + 5 continentes (extranjero con borde punteado). 5 modos de color: líder, avance, margen, participación, candidatx. click para agregar al lienzo
12. **actas pendientes** — sección final. 6 tarjetas resumen + 6 tablas detalladas (deptos, continentes, provs top 15, países top 15, distritos top 20, ciudades top 20). todas las tablas son sortable por click en headers. proyección de votos pendientes con método híbrido: avance ≥70% → extrapolación por actas, <70% → baseline participación 2018 (perú) o 40% (extranjero)

### paleta y tipografía

- fondo: blanco (`#fff`)
- acento: `#5a7a3a` (verde oliva)
- tipografía: `'Fira Code', 'JetBrains Mono', monospace`
- UI labels en lowercase
- patrón de back-link: `← toolbox/`
- 3 decimales en todos los porcentajes y pp

### padrón electoral

hardcodeado en el JS (fuente: ONPE):

```
total: 27,325,432 electores
  perú: 26,114,619
  extranjero: 1,210,813
```

solo disponible a nivel departamento + extranjero virtual. sub-niveles no tienen padrón directo; la proyección de votos pendientes usa extrapolación por actas para esos.

### funciones clave del JS

| función | descripción |
|---------|-------------|
| `discover()` | autodescubre snapshots (manifest → github API) |
| `fetchAll(files)` | descarga y parsea todos los JSONs |
| `buildDB(results)` | construye la estructura interna DB con snapshots indexados |
| `activeIndices()` | retorna índices de snapshots activos (no inactivos) |
| `votesAt(key, code, idx)` | votos de candidatx `code` en nodo `key` en snapshot `idx` |
| `totalValidVotesAt(key, idx)` | total votos válidos (excluyendo blanco/nulo) |
| `valueAt(key, code, idx, mode)` | valor en modo `pct` o `votes` |
| `renderGrid()` | renderiza todos los gráficos SVG del lienzo |
| `renderDuelos()` | renderiza sección de duelos |
| `renderMovements()` | renderiza panel de movimientos (resumen, tabla %, origen, eventos) |
| `renderInforme()` | renderiza tarjetas de informe nacional |
| `renderBrowser()` | renderiza navegador territorial con drill genérico |
| `renderMap()` | renderiza mapa de grilla SVG con 5 modos |
| `renderPending()` | renderiza sección de actas pendientes con proyecciones |
| `renderCastilloCompare()` | renderiza tabla comparativa Castillo 2021 vs Sánchez |
| `castilloPct(key)` | retorna % de Castillo 2021 para un nodo |
| `expectedParticipationPct(key)` | retorna % participación esperada (baseline 2018 / 40% ext.) |
| `padronOf(key)` | retorna electores hábiles del nodo (solo nivel depto) |
| `detectMovements()` | detecta flips, cambios de puesto, márgenes, saltos de avance |

### debug

`window.DEBUG` expone: `{DB, CASTILLO, PARTICIPACION, state, castilloPct, activeIndices}`

---

## estado político al último snapshot

**s23 · 15 abr 18:57 UTC (23:57 hora lima) · avance 91.72%**

```
1°  Keiko Fujimori        17.042%   2,641,556 votos
2°  Roberto Sánchez P.    12.069%   1,870,690
3°  Rafael López Aliaga   11.851%   1,836,964
4°  Jorge Nieto           11.058%   1,713,990
5°  Ricardo Belmont       10.151%   1,573,347
```

brecha 2°↔3° (sánchez−LA): **0.218pp / 33,726 votos**

### cronología de flips

| snapshot | avance | evento |
|----------|--------|--------|
| s14 (84.52%) | 14 abr 23:31 | **PRIMER FLIP**: Sánchez supera Nieto → 3° puesto |
| s16 (89.77%) | 15 abr 06:22 | **SEGUNDO FLIP**: Sánchez supera López Aliaga → **2° puesto** |

### extranjero

avance 45.5% (1,132/2,543 actas). estancado varias horas, moviéndose lentamente. ~352k votos esperados pendientes (asumiendo 40% participación).

### proyección de votos pendientes

método híbrido: avance ≥70% → extrapolación por actas. avance <70% → baseline participación 2018.

```
perú:       ~1.36M votos pendientes
extranjero: ~352k votos pendientes
total:      ~1.71M votos pendientes
```

### herencia castillo 2021

sánchez palomino ganó 8 de los 12 "strongholds" de castillo (deptos donde castillo sacó ≥30% en 2021).

---

## contexto para continuar el trabajo

### cómo generar un nuevo snapshot

1. el usuario sube `candidatos.csv` y `totales.csv` como attachments
2. ejecutar `cd /home/claude && python3 build_data.py`
3. verificar output y compartir el JSON generado
4. el usuario lo sube a `snapshots/` en el repo

`build_data.py` lee de `/mnt/user-data/uploads/` y escribe a `/mnt/user-data/outputs/`. si el script no existe en `/home/claude/`, se puede recrear a partir del código documentado arriba o pedir al usuario que lo proporcione.

### cómo editar el dashboard

1. el archivo a editar es `/mnt/user-data/outputs/index.html`
2. después de cada edición, validar sintaxis JS con:
   ```bash
   python3 -c "
   import re, subprocess
   html = open('/mnt/user-data/outputs/index.html').read()
   m = re.search(r'<script>(.+)</script>', html, re.DOTALL)
   open('/tmp/check.mjs','w').write(m.group(1))
   r = subprocess.run(['node','--check','/tmp/check.mjs'], capture_output=True, text=True)
   print(r.stderr[:300] if r.stderr else '(ok)')
   "
   ```
3. compartir con `present_files`

### convenciones de código

- fondo blanco (nunca dark theme)
- tipografía: Fira Code / JetBrains Mono
- acento: `#5a7a3a`
- UI labels en lowercase
- lenguaje inclusivo con "x" (candidatxs, seleccionadxs)
- 3 decimales en todos los porcentajes
- `fmtS()` para magnitudes con sufijo (k, M)
- `fmt()` para números con separador de miles

### qué NO hacer (decisiones ya tomadas)

- **no zoom** — se implementó y removió varias veces, el usuario no lo quiere
- **no simulador de votos** — el usuario no lo quiere
- **no proyecciones lineales de resultado final** — solo reportar datos reales
- **no automatización via VPS** — se intentó con Elástika (IP peruana) pero ONPE filtra por ASN de datacenter

### features discutidos pero no implementados

- barra de búsqueda rápida para navegar lugares
- panel de ranking nacional siempre visible
- sección "deptos con conteo completo"
- export/share de vista específica via URL
- matriz cruzada líder×snapshot para detectar flips

---

## dependencias

### dashboard (index.html)
ninguna. standalone, vanilla JS.

### scraper (onpe_scraper.py)
- python 3.10+
- `httpx[http2]`
- `pandas`
- `pyarrow`

### build_data.py
- python 3.10+
- `pandas`

---

## licencia

proyecto de monitoreo ciudadano con fines informativos y de investigación.

---

*carlos zevallos trigoso · pucp · abril 2026*
