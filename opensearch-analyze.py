#!/usr/bin/env python3
"""OpenSearch service analyzer — CLI report of cluster/index/node health.

Performs the same analysis the opensearch-metrics Spring Boot service does
(polling /_stats and /_nodes/stats) but prints a human-readable report
instead of writing documents back into OpenSearch.
"""

import argparse
import json
import os
import sys
import textwrap
import time
from datetime import datetime
from urllib.request import urlopen
from urllib.error import URLError, HTTPError

DEFAULT_HOST = os.environ.get("OPENSEARCH_HOST", "http://localhost:9200")

HEAP_WARN_PERCENT = 85
CACHE_SAMPLE_MIN = 100
CACHE_HIT_RATIO_WARN = 50.0
QUERY_COLUMN_WRAP = 80
SLOW_QUERY_WARN_MS = 1000


def fetch_json(host: str, path: str, timeout: float = 10.0) -> dict:
    url = f"{host}{path}"
    with urlopen(url, timeout=timeout) as resp:
        return json.load(resp)


def format_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def format_ms(n: float) -> str:
    if n >= 1000:
        return f"{n / 1000:.2f}s"
    return f"{n:.0f}ms"


def collect_cluster_health(host: str) -> dict:
    data = fetch_json(host, "/_cluster/health")
    return {
        "status": data.get("status"),
        "number_of_nodes": data.get("number_of_nodes"),
        "active_shards": data.get("active_shards"),
        "relocating_shards": data.get("relocating_shards"),
        "initializing_shards": data.get("initializing_shards"),
        "unassigned_shards": data.get("unassigned_shards"),
    }


def collect_index_stats(host: str) -> list:
    data = fetch_json(host, "/_stats/search,indexing,store,docs,merge,query_cache,request_cache")
    indices = data.get("indices", {})
    rows = []

    for index_name, entry in sorted(indices.items()):
        if index_name.startswith("."):
            continue

        primaries = entry.get("primaries", {})
        search = primaries.get("search", {})
        indexing = primaries.get("indexing", {})
        docs = primaries.get("docs", {})
        store = primaries.get("store", {})
        merges = primaries.get("merges", {})
        query_cache = primaries.get("query_cache", {})
        request_cache = primaries.get("request_cache", {})

        query_total = search.get("query_total", 0)
        query_time_ms = search.get("query_time_in_millis", 0)
        indexing_total = indexing.get("index_total", 0)
        indexing_failed = indexing.get("index_failed", 0)

        qc_hit = query_cache.get("hit_count", 0)
        qc_miss = query_cache.get("miss_count", 0)
        rc_hit = request_cache.get("hit_count", 0)
        rc_miss = request_cache.get("miss_count", 0)

        avg_query_ms = query_time_ms / query_total if query_total else 0.0
        qc_ratio = qc_hit * 100.0 / (qc_hit + qc_miss) if (qc_hit + qc_miss) else 0.0
        rc_ratio = rc_hit * 100.0 / (rc_hit + rc_miss) if (rc_hit + rc_miss) else 0.0

        rows.append({
            "index": index_name,
            "docs_count": docs.get("count", 0),
            "store_size_bytes": store.get("size_in_bytes", 0),
            "query_total": query_total,
            "avg_query_ms": avg_query_ms,
            "fetch_total": search.get("fetch_total", 0),
            "scroll_total": search.get("scroll_total", 0),
            "indexing_total": indexing_total,
            "indexing_failed": indexing_failed,
            "merges_current": merges.get("current", 0),
            "query_cache_hit_ratio": qc_ratio,
            "query_cache_samples": qc_hit + qc_miss,
            "request_cache_hit_ratio": rc_ratio,
            "request_cache_samples": rc_hit + rc_miss,
        })

    return rows


def collect_node_stats(host: str) -> list:
    data = fetch_json(host, "/_nodes/stats/thread_pool,jvm,breaker")
    nodes = data.get("nodes", {})
    rows = []

    for node_id, node in nodes.items():
        name = node.get("name", node_id)
        search_pool = node.get("thread_pool", {}).get("search", {})
        heap = node.get("jvm", {}).get("mem", {})
        breakers = node.get("breakers", {})

        rows.append({
            "node": name,
            "search_queue": search_pool.get("queue", 0),
            "search_rejected": search_pool.get("rejected", 0),
            "search_active": search_pool.get("active", 0),
            "heap_used_percent": heap.get("heap_used_percent", 0),
            "heap_used_bytes": heap.get("heap_used_in_bytes", 0),
            "heap_max_bytes": heap.get("heap_max_in_bytes", 0),
            "breaker_parent_tripped": breakers.get("parent", {}).get("tripped", 0),
            "breaker_fielddata_tripped": breakers.get("fielddata", {}).get("tripped", 0),
            "breaker_request_tripped": breakers.get("request", {}).get("tripped", 0),
        })

    return sorted(rows, key=lambda r: r["node"])


def collect_top_queries(host: str, query_type: str = "latency", limit: int = 10):
    """Long-running / expensive queries via the Query Insights plugin.

    Returns None if the plugin isn't installed/enabled on the target cluster
    (the endpoint 404s or is rejected), so the report can degrade gracefully.
    """
    try:
        data = fetch_json(host, f"/_insights/top_queries?type={query_type}&verbose=true")
    except HTTPError as exc:
        if exc.code in (400, 404):
            return None
        raise

    rows = []
    for q in data.get("top_queries", []):
        measurements = q.get("measurements", {})
        source = q.get("source", {})
        query_summary = json.dumps(source.get("query", source), separators=(",", ":"))

        rows.append({
            "id": q.get("id"),
            "timestamp_ms": q.get("timestamp", 0),
            "latency_ms": measurements.get("latency", {}).get("number", 0),
            "cpu_ns": measurements.get("cpu", {}).get("number", 0),
            "memory_bytes": measurements.get("memory", {}).get("number", 0),
            "indices": ",".join(q.get("indices", [])) or "-",
            "search_type": q.get("search_type"),
            "total_shards": q.get("total_shards"),
            "node_id": q.get("node_id"),
            "query": query_summary,
        })

    rows.sort(key=lambda r: r["latency_ms"], reverse=True)
    return rows[:limit]


def build_findings(cluster: dict, indices: list, nodes: list, top_queries=None) -> list:
    findings = []

    if cluster.get("status") in ("yellow", "red"):
        findings.append(f"cluster status is {cluster['status'].upper()}")
    if cluster.get("unassigned_shards", 0) > 0:
        findings.append(f"{cluster['unassigned_shards']} unassigned shard(s)")

    for idx in indices:
        if idx["indexing_failed"] > 0:
            findings.append(f"index '{idx['index']}': {idx['indexing_failed']} failed indexing operation(s)")
        if idx["query_cache_samples"] >= CACHE_SAMPLE_MIN and idx["query_cache_hit_ratio"] < CACHE_HIT_RATIO_WARN:
            findings.append(
                f"index '{idx['index']}': low query cache hit ratio ({idx['query_cache_hit_ratio']:.1f}%)"
            )

    for node in nodes:
        if node["heap_used_percent"] >= HEAP_WARN_PERCENT:
            findings.append(f"node '{node['node']}': JVM heap at {node['heap_used_percent']}%")
        if node["search_rejected"] > 0:
            findings.append(f"node '{node['node']}': {node['search_rejected']} rejected search task(s)")
        tripped = node["breaker_parent_tripped"] + node["breaker_fielddata_tripped"] + node["breaker_request_tripped"]
        if tripped > 0:
            findings.append(f"node '{node['node']}': circuit breaker tripped {tripped} time(s)")

    if top_queries:
        slow = [q for q in top_queries if q["latency_ms"] >= SLOW_QUERY_WARN_MS]
        if slow:
            worst = slow[0]
            findings.append(
                f"{len(slow)} long-running quer{'y' if len(slow) == 1 else 'ies'} "
                f">= {SLOW_QUERY_WARN_MS}ms (worst: {worst['latency_ms']}ms on {worst['indices']})"
            )

    return findings


def print_table(headers: list, rows: list, wrap_widths: dict = None) -> None:
    """Print an aligned table. wrap_widths maps column index -> max width;
    cells in that column wrap onto continuation lines instead of being cut off."""
    if not rows:
        print("  (none)")
        return
    wrap_widths = wrap_widths or {}

    def cell_lines(cell, col_idx):
        text = str(cell)
        limit = wrap_widths.get(col_idx)
        if limit:
            return textwrap.wrap(text, width=limit) or [""]
        return [text]

    wrapped_rows = [[cell_lines(cell, i) for i, cell in enumerate(row)] for row in rows]

    widths = [len(h) for h in headers]
    for cols_lines in wrapped_rows:
        for i, lines in enumerate(cols_lines):
            widths[i] = max([widths[i]] + [len(line) for line in lines])

    def fmt_row(cells):
        return "  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))

    print(fmt_row(headers))
    print(fmt_row(["-" * w for w in widths]))
    for cols_lines in wrapped_rows:
        for line_idx in range(max(len(lines) for lines in cols_lines)):
            cells = [lines[line_idx] if line_idx < len(lines) else "" for lines in cols_lines]
            print(fmt_row(cells))


def format_timestamp_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).strftime("%H:%M:%S")


def print_report(host: str, cluster: dict, indices: list, nodes: list, top_queries, query_limit: int) -> None:
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"OpenSearch analysis — {host}  ({ts})")
    print("=" * 70)

    print("\nCluster")
    print_table(
        ["status", "nodes", "active_shards", "relocating", "initializing", "unassigned"],
        [[
            cluster.get("status"), cluster.get("number_of_nodes"), cluster.get("active_shards"),
            cluster.get("relocating_shards"), cluster.get("initializing_shards"), cluster.get("unassigned_shards"),
        ]],
    )

    print(f"\nIndices ({len(indices)})")
    print_table(
        ["index", "docs", "size", "queries", "avg_query", "index_ops", "failed", "qc_hit%", "rc_hit%"],
        [[
            i["index"], i["docs_count"], format_bytes(i["store_size_bytes"]), i["query_total"],
            format_ms(i["avg_query_ms"]), i["indexing_total"], i["indexing_failed"],
            f"{i['query_cache_hit_ratio']:.1f}", f"{i['request_cache_hit_ratio']:.1f}",
        ] for i in indices],
    )

    print(f"\nNodes ({len(nodes)})")
    print_table(
        ["node", "search_q", "search_rej", "search_active", "heap%", "breaker_trips"],
        [[
            n["node"], n["search_queue"], n["search_rejected"], n["search_active"], n["heap_used_percent"],
            n["breaker_parent_tripped"] + n["breaker_fielddata_tripped"] + n["breaker_request_tripped"],
        ] for n in nodes],
    )

    if query_limit > 0 and top_queries is None:
        print("\nLong-running queries")
        print("  Query Insights plugin not available on this cluster (GET /_insights/top_queries failed)")
    elif query_limit > 0:
        print(f"\nLong-running queries (top {len(top_queries)} by latency, Query Insights)")
        print_table(
            ["latency", "cpu", "memory", "indices", "shards", "search_type", "at", "query"],
            [[
                format_ms(q["latency_ms"]), format_ms(q["cpu_ns"] / 1_000_000), format_bytes(q["memory_bytes"]),
                q["indices"], q["total_shards"], q["search_type"], format_timestamp_ms(q["timestamp_ms"]),
                q["query"],
            ] for q in top_queries],
            wrap_widths={7: QUERY_COLUMN_WRAP},
        )

    findings = build_findings(cluster, indices, nodes, top_queries)
    print(f"\nFindings ({len(findings)})")
    if findings:
        for f in findings:
            print(f"  ! {f}")
    else:
        print("  no issues detected")
    print()


def run_once(host: str, as_json: bool, query_type: str, query_limit: int) -> int:
    try:
        cluster = collect_cluster_health(host)
        indices = collect_index_stats(host)
        nodes = collect_node_stats(host)
        top_queries = collect_top_queries(host, query_type, query_limit) if query_limit > 0 else None
    except HTTPError as exc:
        print(f"OpenSearch returned HTTP {exc.code} for {exc.url}: {exc.read().decode(errors='replace')[:200]}", file=sys.stderr)
        return 1
    except URLError as exc:
        print(f"Cannot reach OpenSearch at {host}: {exc.reason}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "cluster": cluster,
            "indices": indices,
            "nodes": nodes,
            "top_queries": top_queries,
            "findings": build_findings(cluster, indices, nodes, top_queries),
        }, indent=2))
    else:
        print_report(host, cluster, indices, nodes, top_queries, query_limit)

    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Analyze an OpenSearch cluster (index stats, node health, cluster status).")
    parser.add_argument("--host", default=DEFAULT_HOST, help=f"OpenSearch base URL (default: {DEFAULT_HOST}, env OPENSEARCH_HOST)")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON instead of a text report")
    parser.add_argument("--watch", action="store_true", help="Repeat the analysis on an interval until interrupted")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between runs in --watch mode (default: 10)")
    parser.add_argument("--long-queries-type", choices=["latency", "cpu", "memory"], default="latency",
                         help="Rank long-running queries by this metric via the Query Insights plugin (default: latency)")
    parser.add_argument("--long-queries-limit", type=int, default=10,
                         help="Number of long-running queries to show, 0 to disable this section (default: 10)")
    args = parser.parse_args()

    if not args.watch:
        return run_once(args.host, args.json, args.long_queries_type, args.long_queries_limit)

    try:
        while True:
            run_once(args.host, args.json, args.long_queries_type, args.long_queries_limit)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
