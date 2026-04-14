# Setup — snapshots automáticos ONPE

## Archivos que debes subir al repo `conteoONPE`

Estructura final del repo:

```
conteoONPE/
├── index.html                          (ya lo tienes)
├── onpe_data_*.json                    (los snapshots ya subidos)
├── onpe_2026_scraper_v3.py             (ya te lo pasé antes — lo subes aquí)
├── build_data.py                       (nuevo, te lo paso ahora)
├── retention.py                        (nuevo, te lo paso ahora)
└── .github/
    └── workflows/
        └── snapshot.yml                (nuevo, te lo paso ahora)
```

## Pasos

### 1. Sube los 4 archivos nuevos al repo

- `onpe_2026_scraper_v3.py` → raíz del repo
- `build_data.py` → raíz del repo
- `retention.py` → raíz del repo
- `snapshot.yml` → dentro de `.github/workflows/` (crea las carpetas)

Commit + push.

### 2. Habilita permisos de escritura para Actions

En GitHub: `Settings` → `Actions` → `General` → `Workflow permissions` → selecciona **"Read and write permissions"** → Save.

Sin esto, el workflow no puede commitear snapshots nuevos.

### 3. Prueba manual (¡importante!)

En tu repo: pestaña `Actions` → selecciona `snapshot ONPE` en la barra izquierda → click `Run workflow` (dropdown arriba a la derecha) → deja `incluir_distritos: false` → `Run workflow`.

El workflow aparecerá ejecutándose. Click sobre él para ver el log en tiempo real. Un snapshot exitoso se ve así:

```
✓ Checkout repo
✓ Setup Python
✓ Install dependencies
✓ Run scraper
  → avance: 76.xx%
✓ Build JSON
  → onpe_data_20260414T1600Z_76.20pct.json
✓ Apply retention policy
  → retention: 7 conservados, 0 borrados
✓ Commit and push
  → ✓ push exitoso
```

Revisa tu repo — deberías ver un commit nuevo del `github-actions[bot]` con el snapshot.

### 4. Si falla

Copia el log completo del paso que falló y pégamelo aquí. Ajusto, pusheas el fix, vuelves a correr manual.

**Errores comunes y sus fixes:**

- `403 git push`: no habilitaste los permisos del paso 2
- `ModuleNotFoundError: pandas`: raro, ya lo tengo en el install
- `FileNotFoundError: totales.csv`: el scraper no generó el CSV — probablemente ONPE dio timeout
- Timeout general (15 min): el scraper se demoró, prueba con `--no-distrito`
- `nothing to commit`: no es error — es lo que pasa si el avance no cambió

### 5. Activar el cron

Cuando hayas corrido manual 2-3 veces sin problemas, edita `.github/workflows/snapshot.yml` y **descomenta** estas líneas:

```yaml
  schedule:
    - cron: '*/30 * * * *'
```

Commit + push. Desde ese momento el workflow se ejecutará solo cada 30 minutos (aprox.; GitHub puede tardar hasta 15 min extra).

### 6. Notificaciones

GitHub te envía email automáticamente cuando un workflow falla. Si quieres cambiarlo:
`Settings` (tu perfil) → `Notifications` → `Actions`.

## Política de retención

Cada corrida, después de generar el snapshot nuevo:

- Los snapshots de las **últimas 24h** se conservan TODOS (cada 30 min → ~48 archivos)
- Los snapshots de **más de 24h** se adelgazan a **1 por hora** (se queda el más reciente de cada hora)
- Siempre se conserva el snapshot más antiguo (baseline) y el más reciente

Ejemplo: después de 3 días de elección tendrías:
- ~48 snapshots de las últimas 24h (cada 30 min)
- ~24 snapshots del día anterior (uno por hora)
- ~24 snapshots de hace 2 días (uno por hora)
- 1 snapshot baseline del inicio

Total: ~100 archivos × ~1 MB = ~100 MB. Razonable para un repo público.

## Generar snapshots manuales con distritos

El cron automático corre **sin distritos** (más rápido, ~30s). Si quieres un snapshot granular con distritos en algún momento:

1. `Actions` → `snapshot ONPE` → `Run workflow`
2. Cambia `incluir_distritos` a `true`
3. `Run workflow`

Tarda ~5 minutos. Solo conviene hacerlo unas pocas veces, no cada 30 min.

## Desactivar temporalmente

Si quieres parar los snapshots automáticos (elecciones terminaron, estás probando otras cosas):

- Opción A: `Actions` tab → dropdown del workflow → `Disable workflow`
- Opción B: vuelve a comentar las líneas `schedule: - cron:` en el YAML

## Archivos clave

- **snapshot.yml**: workflow de GitHub Actions
- **onpe_2026_scraper_v3.py**: scraper (corre en el runner, no necesita tu Mac)
- **build_data.py**: convierte CSVs del scraper en el JSON que consume el HTML
- **retention.py**: aplica la política de retención
