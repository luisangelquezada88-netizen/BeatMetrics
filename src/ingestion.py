from datetime import UTC, datetime
import os
import time

from src.config import get_project_config
from src.logger import get_logger
from src.search import (
    SpotifyQuotaExhausted,
    SpotifySearchError,
    get_retry_stats,
    reset_retry_stats,
    search_tracks,
)
from src.transform import transform_search_results
from src.mongo import (
    close_mongo_client,
    ensure_indexes,
    get_collection,
    get_query_cursors,
    insert_one_document,
    upsert_many_tracks,
    upsert_query_cursor,
)


logger = get_logger(__name__)


def build_queries(mode: str = "full") -> list[str]:
    """Construye la matriz término × año.

    - full: todos los años del alcance (2018-2025).
    - daily: solo los últimos `recent_years`, para no competir por la
      misma cuota con el barrido completo del lunes.
    """
    config = get_project_config()
    settings = config["settings"]

    query_terms = settings["spotify"]["query_terms"]
    start_year = settings["project"]["start_year"]
    end_year = settings["project"]["end_year"]

    if mode == "daily":
        recent_years = int(settings["project"].get("recent_years", 3))
        start_year = max(start_year, end_year - recent_years + 1)

    queries = []
    for term in query_terms:
        for year in range(start_year, end_year + 1):
            queries.append(f"{term} year:{year}")

    return queries


def _seed_query_cursors(queries: list[str], max_offset: int) -> dict[str, dict]:
    """Crea el cursor de cada query a partir de lo ya capturado.

    Un cursor guarda desde qué offset debe continuar la próxima corrida.
    El pipeline anterior nunca pasó del offset 490 (50 páginas), así que
    toda query ya ejecutada reanuda en 500; las nunca pedidas, en 0.
    """
    cursors = get_query_cursors()
    missing = [q for q in queries if q not in cursors]
    if not missing:
        return cursors

    counts = {
        doc["_id"]: doc["n"]
        for doc in get_collection("curated_tracks").aggregate(
            [
                {"$match": {"search_query": {"$in": missing}}},
                {"$group": {"_id": "$search_query", "n": {"$sum": 1}}},
            ]
        )
    }

    seeded = 0
    seed_time = datetime.now(UTC).isoformat()
    for query in missing:
        captured = counts.get(query, 0)
        if captured <= 0:
            # Query nueva: jamás se pidió, se arranca en el offset 0.
            next_offset = 0
        else:
            # Zona inédita de toda query ya ejecutada: el viejo pipeline
            # se detenía en 490. Sembrar en 500 evita re-pedir ~49 páginas
            # de bajo rendimiento por query.
            next_offset = min(max_offset, 500)
        upsert_query_cursor(
            query, next_offset=next_offset, exhausted=False, last_run_at=seed_time
        )
        seeded += 1

    logger.info("Cursors sembrados desde lo ya capturado: %s", seeded)
    return get_query_cursors()


def _order_queries(queries: list[str], cursors: dict[str, dict]) -> list[str]:
    """Rotación justa: primero las queries tocadas hace más tiempo.

    Evita que las primeras de la matriz consuman siempre el presupuesto.
    """
    def sort_key(query: str):
        cursor = cursors.get(query, {})
        return (
            cursor.get("last_run_at") or "",
            cursor.get("next_offset") or 0,
            query,
        )

    return sorted(queries, key=sort_key)


def run_ingestion(
    mode: str = "full",
    limit_queries: int | None = None,
    min_popularity: int | None = None,
    pages_per_query: int | None = None,
    request_budget: int | None = None,
    shard_index: int = 0,
    num_shards: int = 1,
    shard_delay_seconds: int = 0,
) -> dict:
    """Ejecuta la ingesta con cursor persistente y presupuesto de requests.

    - mode: "full" (matriz completa) o "daily" (años recientes).
    - limit_queries: smoke test (p. ej. 2 queries).
    - min_popularity: filtra tracks bajo el umbral (default: settings).
    - pages_per_query: páginas por query en esta corrida (10 = 100 tracks).
    - request_budget: techo de requests de la corrida (cuota Spotify).
    - shard_index/num_shards: reparte queries entre jobs paralelos.
    - shard_delay_seconds: espera inicial para escalonar shards.

    Cada query avanza su cursor en `query_cursors`, así que una corrida
    nunca vuelve a pedir las páginas que ya están en la base.
    """
    config = get_project_config()
    settings = config["settings"]

    limit = settings["spotify"]["limit"]
    sleep_seconds = settings["spotify"]["sleep_seconds"]
    max_offset = int(settings["spotify"].get("max_search_offset", 990))
    pages_cap = pages_per_query or int(settings["spotify"].get("pages_per_query_run", 10))
    env_budget = os.getenv("REQUEST_BUDGET")
    budget = request_budget or (
        int(env_budget) if env_budget else int(settings["spotify"].get("request_budget", 400))
    )
    if min_popularity is None:
        min_popularity = int(settings["project"].get("min_popularity", 0))

    queries = build_queries(mode=mode)
    if num_shards > 1:
        queries = [q for i, q in enumerate(queries) if i % num_shards == shard_index]

    if shard_delay_seconds > 0:
        logger.info("Shard %s: espera inicial de %s s para escalonar carga",
                    shard_index, shard_delay_seconds)
        time.sleep(shard_delay_seconds)

    ensure_indexes("curated_tracks")
    # Se siembra la matriz completa antes de recortar por --limit-queries,
    # para que un smoke test no deje cursores faltantes.
    cursors = _seed_query_cursors(queries, max_offset=max_offset)

    if limit_queries:
        queries = queries[:limit_queries]

    reset_retry_stats()

    # Circuit breaker: 1 request de sondeo. Si la cuota sigue quemada del
    # full anterior, se registra skip y se sale en verde sin quemar cientos
    # de requests inútiles.
    total_requests = 0
    if queries:
        try:
            search_tracks(queries[0], limit=1, offset=0)
            total_requests += 1
        except SpotifyQuotaExhausted as error:
            logger.error("Cuota quemada al sondear: skip de la corrida (Retry-After %ss)",
                         error.retry_after)
            skipped_id = insert_one_document("ingestion_runs", {
                "run_started_at": datetime.now(UTC).isoformat(),
                "run_finished_at": datetime.now(UTC).isoformat(),
                "mode": mode,
                "status": "skipped_quota",
                "stop_reason": "quota_exhausted",
                "quota_retry_after_seconds": error.retry_after,
                "source": "spotify_web_api",
            })
            close_mongo_client()
            return {
                "run_id": str(skipped_id),
                "status": "skipped_quota",
                "stop_reason": "quota_exhausted",
                "total_queries": 0,
                "failed_queries": 0,
                "total_requests": 1,
                "total_pages": 0,
                "total_tracks_transformed": 0,
                "total_filtered_by_popularity": 0,
                "total_upserts": 0,
                "total_inserted_new": 0,
                "total_matched_existing": 0,
                "http_429_count": 0,
                "popularity_stats": {"min": None, "max": None, "avg": None,
                                      "null_count": 0, "total_sampled": 0},
            }
        except SpotifySearchError as error:
            logger.warning("Sondeo fallido (transitorio, se continúa): %s", error)

    run_started_at = datetime.now(UTC).isoformat()

    total_queries = 0
    total_tracks_transformed = 0
    total_filtered_by_popularity = 0
    total_upserts = 0
    total_inserted = 0
    total_matched = 0
    total_pages = 0
    failed_queries = 0
    exhausted_now = 0
    query_summaries = []
    errors = []
    all_popularities: list = []
    quota_exhausted = False
    quota_retry_after: int | None = None
    stop_reason = "completed"

    ordered = _order_queries(queries, cursors)
    pending = [q for q in ordered if not (cursors.get(q) or {}).get("exhausted")]

    logger.info("Iniciando ingesta (mode=%s, budget=%s requests, páginas/query=%s)",
                mode, budget, pages_cap)
    logger.info("Queries en matriz: %s | con cursor pendiente: %s", len(queries), len(pending))

    if not pending:
        stop_reason = "all_queries_exhausted"
        logger.warning("Todas las queries llegaron al tope de resultados de la API")

    for query in pending:
        if total_requests >= budget:
            stop_reason = "budget_reached"
            logger.info("Presupuesto de %s requests alcanzado; se corta limpio", budget)
            break

        cursor_doc = cursors.get(query) or {}
        offset = int(cursor_doc.get("next_offset") or 0)
        start_offset = offset

        query_tracks_transformed = 0
        query_upserts = 0
        query_inserted = 0
        query_matched = 0
        query_requests = 0
        query_exhausted = False
        empty_pages = 0

        while (
            offset <= max_offset
            and query_requests < pages_cap
            and total_requests < budget
        ):
            logger.info("Query %s | offset=%s", query, offset)

            try:
                search_result = search_tracks(query, limit=limit, offset=offset)
            except SpotifyQuotaExhausted as error:
                logger.error("Cuota Spotify agotada (Retry-After %ss): abortando corrida",
                             error.retry_after)
                errors.append({"query": query, "offset": offset, "error": str(error)})
                quota_exhausted = True
                quota_retry_after = error.retry_after
                stop_reason = "quota_exhausted"
                break
            except SpotifySearchError as error:
                logger.error("Query fallida %s offset=%s: %s", query, offset, error)
                errors.append({"query": query, "offset": offset, "error": str(error)})
                break

            query_requests += 1
            total_requests += 1

            curated_documents = transform_search_results(search_result, query)
            page_size = len(curated_documents)
            all_popularities.extend([d.get("popularity") for d in curated_documents])

            if min_popularity > 0:
                before = len(curated_documents)
                curated_documents = [
                    d for d in curated_documents
                    if isinstance(d.get("popularity"), (int, float))
                    and d["popularity"] >= min_popularity
                ]
                total_filtered_by_popularity += before - len(curated_documents)

            if curated_documents:
                try:
                    outcome = upsert_many_tracks("curated_tracks", curated_documents)
                except Exception as error:
                    logger.error("Upsert fallido %s offset=%s: %s", query, offset, error)
                    errors.append({"query": query, "offset": offset, "error": f"upsert: {error}"})
                    offset += limit
                    continue

                query_tracks_transformed += len(curated_documents)
                total_tracks_transformed += len(curated_documents)
                query_upserts += outcome["inserted"] + outcome["matched"] + outcome["modified"]
                query_inserted += outcome["inserted"]
                query_matched += outcome["matched"] + outcome["modified"]
                total_upserts += outcome["inserted"] + outcome["matched"] + outcome["modified"]
                total_inserted += outcome["inserted"]
                total_matched += outcome["matched"] + outcome["modified"]
            else:
                outcome = {"inserted": 0}

            offset += limit
            total_pages += 1

            # Una página corta NO significa fin de resultados: Spotify
            # devuelve páginas incompletas a mitad de camino (por eso
            # "techno year:2020" quedó con 4 docs de miles). Solo se da por
            # agotada tras 2 páginas vacías consecutivas.
            if page_size == 0:
                empty_pages += 1
            else:
                empty_pages = 0

            if empty_pages >= 2 or offset > max_offset:
                query_exhausted = True
                logger.info("Fin de resultados para %s en offset=%s", query, offset - limit)

            next_offset = max_offset + limit if query_exhausted else offset
            upsert_query_cursor(
                query,
                next_offset=next_offset,
                exhausted=query_exhausted,
                stats={"lifetime_requests": 1, "lifetime_inserted_new": outcome["inserted"]},
            )

            if query_exhausted:
                exhausted_now += 1
                break

            time.sleep(sleep_seconds)

        if query_requests == 0:
            failed_queries += 1

        query_summaries.append({
            "query": query,
            "offset_start": start_offset,
            "offset_end": min(offset, max_offset + limit),
            "requests_executed": query_requests,
            "tracks_found": query_tracks_transformed,
            "upserts_executed": query_upserts,
            "inserted_new": query_inserted,
            "matched_existing": query_matched,
            "exhausted": query_exhausted,
        })
        total_queries += 1

        if quota_exhausted:
            logger.warning("Abortando queries restantes por cuota agotada")
            break

    close_mongo_client()

    present = [v for v in all_popularities if isinstance(v, (int, float))]
    popularity_stats = {
        "min": min(present) if present else None,
        "max": max(present) if present else None,
        "avg": round(sum(present) / len(present), 2) if present else None,
        "null_count": len(all_popularities) - len(present),
        "total_sampled": len(all_popularities),
    }
    retry_stats = get_retry_stats()

    status = "completed"
    if quota_exhausted:
        status = "quota_exhausted"
    elif errors:
        status = "completed_with_errors"

    logger.info(
        "Resumen: requests=%s/%s | páginas=%s | nuevos=%s | ya existentes=%s | 429=%s | stop=%s",
        total_requests, budget, total_pages, total_inserted, total_matched,
        retry_stats["http_429"], stop_reason,
    )

    run_document = {
        "run_started_at": run_started_at,
        "run_finished_at": datetime.now(UTC).isoformat(),
        "mode": mode,
        "min_popularity": min_popularity,
        "quota_exhausted": quota_exhausted,
        "quota_retry_after_seconds": quota_retry_after,
        "shard_index": shard_index,
        "num_shards": num_shards,
        "request_budget": budget,
        "pages_per_query_run": pages_cap,
        "stop_reason": stop_reason,
        "http_429_count": retry_stats["http_429"],
        "retry_after_max_seconds": retry_stats["retry_after_max"],
        "total_queries": total_queries,
        "failed_queries": failed_queries,
        "queries_exhausted_now": exhausted_now,
        "total_requests": total_requests,
        "total_pages": total_pages,
        "total_tracks_transformed": total_tracks_transformed,
        "total_filtered_by_popularity": total_filtered_by_popularity,
        "total_upserts": total_upserts,
        "total_inserted_new": total_inserted,
        "total_matched_existing": total_matched,
        "popularity_stats": popularity_stats,
        "query_summaries": query_summaries,
        "errors": errors[:50],
        "source": "spotify_web_api",
        "collection_target": "curated_tracks",
        "status": status,
    }

    inserted_id = insert_one_document("ingestion_runs", run_document)

    return {
        "run_id": str(inserted_id),
        "status": status,
        "stop_reason": stop_reason,
        "total_queries": total_queries,
        "failed_queries": failed_queries,
        "total_requests": total_requests,
        "total_pages": total_pages,
        "total_tracks_transformed": total_tracks_transformed,
        "total_filtered_by_popularity": total_filtered_by_popularity,
        "total_upserts": total_upserts,
        "total_inserted_new": total_inserted,
        "total_matched_existing": total_matched,
        "http_429_count": retry_stats["http_429"],
        "popularity_stats": popularity_stats,
    }
