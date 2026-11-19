import argparse

from src.ingestion import run_ingestion


def main() -> None:
    parser = argparse.ArgumentParser(description="Ingesta Spotify -> MongoDB")
    parser.add_argument("--mode", choices=["full", "daily"], default="full")
    parser.add_argument("--limit-queries", type=int, default=None,
                        help="Smoke test: ejecuta solo N queries")
    parser.add_argument("--min-popularity", type=int, default=None,
                        help="Umbral de popularity (default: settings)")
    parser.add_argument("--max-pages", "--pages-per-query", dest="pages_per_query",
                        type=int, default=None,
                        help="Páginas (de 10 tracks) por query en esta corrida")
    parser.add_argument("--request-budget", type=int, default=None,
                        help="Techo de requests de la corrida (cuota Spotify)")
    parser.add_argument("--shard-index", type=int, default=0)
    parser.add_argument("--num-shards", type=int, default=1)
    parser.add_argument("--shard-delay-seconds", type=int, default=0,
                        help="Espera inicial para escalonar shards")
    args = parser.parse_args()

    result = run_ingestion(
        mode=args.mode,
        limit_queries=args.limit_queries,
        min_popularity=args.min_popularity,
        pages_per_query=args.pages_per_query,
        request_budget=args.request_budget,
        shard_index=args.shard_index,
        num_shards=args.num_shards,
        shard_delay_seconds=args.shard_delay_seconds,
    )

    print("Ingesta completada")
    print(f"Status: {result['status']} (stop: {result['stop_reason']})")
    print(f"Run ID: {result['run_id']}")
    print(f"Total de queries: {result['total_queries']} (fallidas: {result['failed_queries']})")
    print(f"Total de requests: {result['total_requests']} | 429s: {result['http_429_count']}")
    print(f"Tracks transformados: {result['total_tracks_transformed']}")
    print(f"Filtrados por popularidad: {result['total_filtered_by_popularity']}")
    print(f"Upserts ejecutados: {result['total_upserts']}")
    print(f"  Nuevos (insertados): {result['total_inserted_new']}")
    print(f"  Ya existentes (emparejados): {result['total_matched_existing']}")
    print(f"Popularity stats: {result['popularity_stats']}")


if __name__ == "__main__":
    main()
