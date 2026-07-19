#!/usr/bin/env python3
"""OpenSearch service analyzer — CLI report of cluster/index/node health.

Performs the same analysis the opensearch-metrics Spring Boot service does
(polling /_stats and /_nodes/stats) but prints a human-readable report
instead of writing documents back into OpenSearch.
"""

import argparse
import base64
import getpass
import json
import os
import ssl
import sys
import textwrap
import time
from datetime import datetime
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env file next to this script into
    os.environ, without overriding variables the shell already set."""
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    try:
        with open(env_path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DEFAULT_HOST = os.environ.get("OPENSEARCH_HOST", "http://localhost:9200")
DEFAULT_USER = os.environ.get("OPENSEARCH_USER")
DEFAULT_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD")
DEFAULT_CA_CERT = os.environ.get("OPENSEARCH_CA_CERT")
DEFAULT_INSECURE = os.environ.get("OPENSEARCH_INSECURE", "").strip().lower() in ("1", "true", "yes")

HEAP_WARN_PERCENT = 85
DISK_WATERMARK_DEFAULT_LOW = 85.0
DISK_WATERMARK_DEFAULT_HIGH = 90.0
DISK_WATERMARK_DEFAULT_FLOOD = 95.0
CACHE_SAMPLE_MIN = 100
CACHE_HIT_RATIO_WARN = 50.0
QUERY_COLUMN_WRAP = 80
SLOW_QUERY_WARN_MS = 1000


class OpenSearchClient:
    """Wraps host + auth + TLS settings so collectors don't juggle them individually."""

    def __init__(self, host: str, username: str = None, password: str = None,
                 verify_tls: bool = True, ca_cert: str = None, timeout: float = 10.0):
        self.host = host
        self.timeout = timeout

        self._auth_header = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self._auth_header = f"Basic {token}"

        self._ssl_context = None
        if host.startswith("https://"):
            if ca_cert:
                self._ssl_context = ssl.create_default_context(cafile=ca_cert)
            elif not verify_tls:
                self._ssl_context = ssl._create_unverified_context()

    def get(self, path: str) -> dict:
        req = Request(f"{self.host}{path}")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        kwargs = {"timeout": self.timeout}
        if self._ssl_context is not None:
            kwargs["context"] = self._ssl_context
        with urlopen(req, **kwargs) as resp:
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


def collect_cluster_health(client: OpenSearchClient) -> dict:
    data = client.get("/_cluster/health")
    return {
        "status": data.get("status"),
        "number_of_nodes": data.get("number_of_nodes"),
        "active_shards": data.get("active_shards"),
        "relocating_shards": data.get("relocating_shards"),
        "initializing_shards": data.get("initializing_shards"),
        "unassigned_shards": data.get("unassigned_shards"),
    }


def collect_index_stats(client: OpenSearchClient) -> list:
    data = client.get("/_stats/search,indexing,store,docs,merge,query_cache,request_cache")
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


def collect_node_stats(client: OpenSearchClient) -> list:
    data = client.get("/_nodes/stats/thread_pool,jvm,breaker,fs")
    nodes = data.get("nodes", {})
    rows = []

    for node_id, node in nodes.items():
        name = node.get("name", node_id)
        search_pool = node.get("thread_pool", {}).get("search", {})
        heap = node.get("jvm", {}).get("mem", {})
        breakers = node.get("breakers", {})
        fs_total = node.get("fs", {}).get("total", {})

        disk_total_bytes = fs_total.get("total_in_bytes", 0)
        disk_available_bytes = fs_total.get("available_in_bytes", 0)
        disk_used_percent = (
            (disk_total_bytes - disk_available_bytes) * 100.0 / disk_total_bytes if disk_total_bytes else 0.0
        )

        rows.append({
            "node": name,
            "search_queue": search_pool.get("queue", 0),
            "search_rejected": search_pool.get("rejected", 0),
            "search_active": search_pool.get("active", 0),
            "heap_used_percent": heap.get("heap_used_percent", 0),
            "heap_used_bytes": heap.get("heap_used_in_bytes", 0),
            "heap_max_bytes": heap.get("heap_max_in_bytes", 0),
            "disk_used_percent": disk_used_percent,
            "disk_available_bytes": disk_available_bytes,
            "disk_total_bytes": disk_total_bytes,
            "breaker_parent_tripped": breakers.get("parent", {}).get("tripped", 0),
            "breaker_fielddata_tripped": breakers.get("fielddata", {}).get("tripped", 0),
            "breaker_request_tripped": breakers.get("request", {}).get("tripped", 0),
        })

    return sorted(rows, key=lambda r: r["node"])


def parse_watermark_percent(value) -> float:
    """Parse a disk watermark setting into a percent-used threshold, or None
    if it's configured as an absolute size (e.g. "50gb") rather than a percentage."""
    if value is None:
        return None
    text = str(value).strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def collect_disk_watermarks(client: OpenSearchClient) -> dict:
    """Effective disk allocation watermarks (persistent/transient override defaults)."""
    data = client.get("/_cluster/settings?include_defaults=true")

    def watermark_block(scope):
        return data.get(scope, {}).get("cluster", {}).get("routing", {}) \
            .get("allocation", {}).get("disk", {}).get("watermark", {})

    persistent = watermark_block("persistent")
    transient = watermark_block("transient")
    defaults = watermark_block("defaults")

    def pick(key):
        return transient.get(key) or persistent.get(key) or defaults.get(key)

    return {
        "low": parse_watermark_percent(pick("low")),
        "high": parse_watermark_percent(pick("high")),
        "flood_stage": parse_watermark_percent(pick("flood_stage")),
    }


def collect_top_queries(client: OpenSearchClient, query_type: str = "latency", limit: int = 10):
    """Long-running / expensive queries via the Query Insights plugin.

    Returns None if the plugin isn't installed/enabled on the target cluster
    (the endpoint 404s or is rejected), so the report can degrade gracefully.
    """
    try:
        data = client.get(f"/_insights/top_queries?type={query_type}&verbose=true")
    except HTTPError as exc:
        if exc.code in (400, 403, 404):
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


def build_findings(cluster: dict, indices: list, nodes: list, top_queries=None, watermarks: dict = None) -> list:
    findings = []
    watermarks = watermarks or {}

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

    flood_wm = watermarks.get("flood_stage")
    high_wm = watermarks.get("high")
    low_wm = watermarks.get("low")

    for node in nodes:
        if node["heap_used_percent"] >= HEAP_WARN_PERCENT:
            findings.append(f"node '{node['node']}': JVM heap at {node['heap_used_percent']}%")
        if node["search_rejected"] > 0:
            findings.append(f"node '{node['node']}': {node['search_rejected']} rejected search task(s)")
        tripped = node["breaker_parent_tripped"] + node["breaker_fielddata_tripped"] + node["breaker_request_tripped"]
        if tripped > 0:
            findings.append(f"node '{node['node']}': circuit breaker tripped {tripped} time(s)")

        disk_pct = node["disk_used_percent"]
        if flood_wm is not None and disk_pct >= flood_wm:
            findings.append(
                f"node '{node['node']}': disk at {disk_pct:.1f}% >= flood-stage watermark ({flood_wm:.0f}%) "
                f"— indices on this node are likely forced read-only"
            )
        elif high_wm is not None and disk_pct >= high_wm:
            findings.append(
                f"node '{node['node']}': disk at {disk_pct:.1f}% >= high watermark ({high_wm:.0f}%) "
                f"— shards are being relocated off this node"
            )
        elif low_wm is not None and disk_pct >= low_wm:
            findings.append(
                f"node '{node['node']}': disk at {disk_pct:.1f}% >= low watermark ({low_wm:.0f}%) "
                f"— no new shards will be allocated to this node"
            )

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


def print_report(host: str, cluster: dict, indices: list, nodes: list, top_queries, query_limit: int,
                  watermarks: dict = None) -> None:
    watermarks = watermarks or {}
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    print(f"OpenSearch analysis — {host}  ({ts})")
    print("=" * 70)

    def fmt_wm(v):
        return f"{v:.0f}%" if v is not None else "n/a"

    print(
        f"Disk watermarks: low={fmt_wm(watermarks.get('low'))} "
        f"high={fmt_wm(watermarks.get('high'))} flood_stage={fmt_wm(watermarks.get('flood_stage'))}"
    )

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
        ["node", "search_q", "search_rej", "search_active", "heap%", "disk%", "breaker_trips"],
        [[
            n["node"], n["search_queue"], n["search_rejected"], n["search_active"], n["heap_used_percent"],
            f"{n['disk_used_percent']:.1f}",
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

    findings = build_findings(cluster, indices, nodes, top_queries, watermarks)
    print(f"\nFindings ({len(findings)})")
    if findings:
        for f in findings:
            print(f"  ! {f}")
    else:
        print("  no issues detected")
    print()


def run_once(client: OpenSearchClient, as_json: bool, query_type: str, query_limit: int) -> int:
    try:
        cluster = collect_cluster_health(client)
        indices = collect_index_stats(client)
        nodes = collect_node_stats(client)
        watermarks = collect_disk_watermarks(client)
        top_queries = collect_top_queries(client, query_type, query_limit) if query_limit > 0 else None
    except HTTPError as exc:
        if exc.code in (401, 403):
            print(
                f"OpenSearch rejected the request with HTTP {exc.code} for {exc.url} — "
                f"the security plugin is likely active; pass --user/--password (or OPENSEARCH_USER/"
                f"OPENSEARCH_PASSWORD)",
                file=sys.stderr,
            )
        else:
            print(f"OpenSearch returned HTTP {exc.code} for {exc.url}: {exc.read().decode(errors='replace')[:200]}", file=sys.stderr)
        return 1
    except URLError as exc:
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            print(
                f"TLS certificate verification failed for {client.host}: {exc.reason} — "
                f"pass --ca-cert <path> for a self-signed cluster CA, or --insecure to skip verification "
                f"(not recommended outside local/dev use)",
                file=sys.stderr,
            )
        else:
            print(f"Cannot reach OpenSearch at {client.host}: {exc.reason}", file=sys.stderr)
        return 1

    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().isoformat(),
            "cluster": cluster,
            "indices": indices,
            "nodes": nodes,
            "top_queries": top_queries,
            "disk_watermarks": watermarks,
            "findings": build_findings(cluster, indices, nodes, top_queries, watermarks),
        }, indent=2))
    else:
        print_report(client.host, cluster, indices, nodes, top_queries, query_limit, watermarks)

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
    parser.add_argument("--user", "-u", default=DEFAULT_USER,
                         help="Basic-auth username for clusters with the security plugin enabled (env OPENSEARCH_USER)")
    parser.add_argument("--password", default=DEFAULT_PASSWORD,
                         help="Basic-auth password (env OPENSEARCH_PASSWORD); if --user is set without this, "
                              "you'll be prompted")
    parser.add_argument("--ca-cert", default=DEFAULT_CA_CERT,
                         help="Path to a CA bundle for verifying a self-signed TLS certificate (env OPENSEARCH_CA_CERT)")
    parser.add_argument("--insecure", "-k", action="store_true", default=DEFAULT_INSECURE,
                         help="Skip TLS certificate verification (env OPENSEARCH_INSECURE); use only for local/dev "
                              "self-signed setups")
    args = parser.parse_args()

    password = args.password
    if args.user and not password:
        password = getpass.getpass(f"Password for {args.user}: ")

    client = OpenSearchClient(
        args.host,
        username=args.user,
        password=password,
        verify_tls=not args.insecure,
        ca_cert=args.ca_cert,
    )

    if not args.watch:
        return run_once(client, args.json, args.long_queries_type, args.long_queries_limit)

    try:
        while True:
            run_once(client, args.json, args.long_queries_type, args.long_queries_limit)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print("\nStopped.")
        return 0


if __name__ == "__main__":
    sys.exit(main())
