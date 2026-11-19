# Runbook / Troubleshooting

## La ingesta marca `completed_with_errors`

1. Abre el log del job → busca `Query fallida` / `Upsert fallido`.
2. El doc en `ingestion_runs` guarda `errors` (máx 50) con query/offset/error.
3. Re-ejecuta solo lo fallido: `python run_ingestion.py --mode daily` (el upsert
   es idempotente; lo ya guardado no se duplica).

## El dataset no crece (o crece muy lento)

1. Corre `python scripts/growth_report.py`: muestra nuevos/request, 429s y
   `stop_reason` por corrida.
2. `nuevos/request` cercano a 0 con miles de "existentes" = esa corrida
   releyó terreno conocido. Revisa que `query_cursors` exista y que el
   workflow NO tenga shards en paralelo.
3. `queries_exhausted_now > 0` de forma sostenida y `nuevos/request` bajo:
   la matriz se está agotando. Añade términos a `spotify.query_terms`
   (cada término suma 8 queries × hasta 1.000 tracks).
4. Para re-sembrar cursores (p. ej. tras corregir un bug de paginación):
   borra la colección y deja que la próxima corrida la recree:
   `db.query_cursors.drop()` en Atlas, o
   `python -c "from src.mongo import get_collection; get_collection('query_cursors').drop()"`.
   El sembrado pone en 0 las queries nunca pedidas y en 500 las ya
   ejecutadas por el pipeline viejo (nunca pasó de 490).

## Subir el presupuesto de requests

`request_budget` en `config/settings.yaml` (o `--request-budget`, o el input
`request_budget` del workflow). Secuencia segura: 400 → 450 → 500, subiendo
solo si `http_429_count` se mantiene en 0 durante varios días. Por encima de
~600 requests/día la app responde 429 con `Retry-After` de hasta 24 h.

## 429 Too Many Requests

El cliente respeta `Retry-After` hasta 300 s con backoff. Dos casos:

- **429 normal**: reintenta solo y sigue. Si ves varios seguidos, sube
  `sleep_seconds` en `config/settings.yaml` (p. ej. a 2) o baja
  `request_budget`.
- **429 con Retry-After gigante (>300 s, p. ej. 85000 s)**: es baneo de cuota
  de ~24 h, no ventana corta. La corrida aborta sola con
  `status: quota_exhausted` guardando progreso y cursor (los upserts ya
  hechos no se pierden, y al día siguiente continúa donde quedó). No
  re-dispates de inmediato: espera al cron del día siguiente.

## 401 Unauthorized

El token cacheado se invalida y se refresca solo una vez. Si persiste:
verifica `SPOTIFY_CLIENT_ID/SECRET` en secretos (rotaron o caducaron).

## `popularity` con muchos nulos

1. Mira `popularity_stats` del run (`min/max/avg/null_count`).
2. Si `null_count` ≈ total: tu app puede estar en Development Mode restringido.
   Corre `python scripts/backfill_popularity.py --dry-run` y luego por lotes:
   `python scripts/backfill_popularity.py --max-batches 5`.
3. Si el backfill también devuelve nulos, documenta el límite y evalúa
   pedir Extended Quota en Spotify Dashboard.

## Filtrar por popularidad

- Permanente: `min_popularity` en `config/settings.yaml`.
- Por corrida: `--min-popularity 20` o secreto/env `MIN_POPULARITY=20`.
- Los filtrados se cuentan en `total_filtered_by_popularity` del run.

## Atlas no conecta desde GHA

- Network Access debe incluir `0.0.0.0/0`.
- Verifica que `MONGO_URI` sea `mongodb+srv://...` con password con caracteres
  URL-escapados (`@` → `%40`, etc.).

## Dashboard vacío ("No hay datos en analysis_tracks")

1. Corre `python scripts/build_analysis.py` (reconstruye `analysis_tracks`).
2. Verifica `test_counts.py`: `curated_tracks` debe tener docs.
3. En Streamlit Cloud, revisa que los Secrets tengan `MONGO_URI` (o `MONGODB_URI`).

## Comandos locales útiles

```powershell
python run_ingestion.py --mode daily --limit-queries 2   # smoke test
python run_ingestion.py --mode daily --min-popularity 20 # con umbral
python run_ingestion.py --mode daily --request-budget 450 --pages-per-query 12
python scripts/growth_report.py                          # ritmo hacia 100k
python scripts/backfill_popularity.py --dry-run
python tests/diagnostics/test_counts.py
```
