"""Pulls cluster/index/node/watermark/query data from the OpenSearch REST API
and shapes it into plain dicts/lists for findings and rendering."""

import json
from urllib.error import HTTPError

from .client import OpenSearchGetter
from .constants import DISK_WATERMARK_DEFAULT_FLOOD, DISK_WATERMARK_DEFAULT_HIGH, DISK_WATERMARK_DEFAULT_LOW


class OpenSearchCollector:
    """Wraps a client; each method pulls and shapes one facet of cluster state."""

    def __init__(self, client: OpenSearchGetter):
        self.client = client

    def cluster_health(self) -> dict:
        data = self.client.get("/_cluster/health")
        return {
            "status": data.get("status"),
            "number_of_nodes": data.get("number_of_nodes"),
            "active_shards": data.get("active_shards"),
            "relocating_shards": data.get("relocating_shards"),
            "initializing_shards": data.get("initializing_shards"),
            "unassigned_shards": data.get("unassigned_shards"),
        }

    def index_stats(self) -> list:
        data = self.client.get("/_stats/search,indexing,store,docs,merge,query_cache,request_cache")
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

    def node_stats(self) -> list:
        data = self.client.get("/_nodes/stats/thread_pool,jvm,breaker,fs")
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

    @staticmethod
    def parse_watermark_percent(value: object) -> float | None:
        """Parse a disk watermark setting into a percent-used threshold, or
        None if it's configured as an absolute size (e.g. "50gb") rather than
        a percentage — an absolute-size watermark can't be safely compared
        against a hardcoded percent default, so it's deliberately left
        unresolved rather than substituted."""
        if value is None:
            return None
        text = str(value).strip()
        if not text.endswith("%"):
            return None
        try:
            return float(text[:-1])
        except ValueError:
            return None

    def disk_watermarks(self) -> dict:
        """Effective disk allocation watermarks (persistent/transient override defaults).

        Falls back to OpenSearch's documented defaults only when a tier
        genuinely has no value for a key at all (e.g. an older cluster whose
        include_defaults response omits it); a tier that does have a value,
        just not a percentage one, is left as None rather than papered over.
        """
        data = self.client.get("/_cluster/settings?include_defaults=true")

        def watermark_block(scope):
            return data.get(scope, {}).get("cluster", {}).get("routing", {}) \
                .get("allocation", {}).get("disk", {}).get("watermark", {})

        persistent = watermark_block("persistent")
        transient = watermark_block("transient")
        defaults = watermark_block("defaults")

        def pick(key):
            return transient.get(key) or persistent.get(key) or defaults.get(key)

        fallback_percent = {
            "low": DISK_WATERMARK_DEFAULT_LOW,
            "high": DISK_WATERMARK_DEFAULT_HIGH,
            "flood_stage": DISK_WATERMARK_DEFAULT_FLOOD,
        }

        result = {}
        for key in ("low", "high", "flood_stage"):
            raw = pick(key)
            result[key] = fallback_percent[key] if raw is None else self.parse_watermark_percent(raw)
        return result

    def top_queries(self, query_type: str = "latency", limit: int = 10) -> list | None:
        """Long-running / expensive queries via the Query Insights plugin.

        Returns None if the plugin isn't installed/enabled on the target
        cluster (the endpoint 404s or is rejected), so the report can degrade
        gracefully. Any other error (5xx, network) propagates so the caller
        can distinguish "plugin not present" from "something actually went
        wrong".
        """
        try:
            data = self.client.get(f"/_insights/top_queries?type={query_type}&verbose=true")
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
