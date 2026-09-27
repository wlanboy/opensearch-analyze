"""Pulls cluster/index/node/watermark/query data from the OpenSearch REST API
and shapes it into plain dicts/lists for findings and rendering."""

import json
import re
from collections import Counter
from urllib.error import HTTPError, URLError

from .client import OpenSearchGetter
from .constants import (
    DISK_WATERMARK_DEFAULT_FLOOD,
    DISK_WATERMARK_DEFAULT_HIGH,
    DISK_WATERMARK_DEFAULT_LOW,
    LARGE_SHARD_BYTES,
    SKIPPED_INDEX_PREFIXES,
)

# ByteSizeValue units accepted by OpenSearch settings (case-insensitive).
_BYTE_UNITS = {
    "b": 1, "k": 1024, "kb": 1024, "m": 1024 ** 2, "mb": 1024 ** 2, "g": 1024 ** 3, "gb": 1024 ** 3,
    "t": 1024 ** 4, "tb": 1024 ** 4, "p": 1024 ** 5, "pb": 1024 ** 5,
}
_BYTE_SIZE_RE = re.compile(r"^(\d+(?:\.\d+)?)\s*([a-z]+)$")

# Which measurement each --long-queries-type ranks by.
TOP_QUERY_SORT_KEYS = {"latency": "latency_ms", "cpu": "cpu_ns", "memory": "memory_bytes"}

SHARDS_PATH = "/_cat/shards?format=json&bytes=b&h=index,shard,prirep,state,store,node,unassigned.reason"
# Block id of a closed index: intentional, so not reported as a finding.
CLOSED_INDEX_BLOCK_ID = "4"


class OpenSearchCollector:
    """Wraps a client; each method pulls and shapes one facet of cluster state."""

    def __init__(self, client: OpenSearchGetter):
        self.client = client
        self._settings: dict | None = None

    def _cluster_settings(self) -> dict:
        """/_cluster/settings with defaults, fetched once per collector (i.e.
        per run) since both the watermarks and the shard limit come from it."""
        if self._settings is None:
            self._settings = self.client.get("/_cluster/settings?include_defaults=true")
        return self._settings

    def cluster_health(self) -> dict:
        data = self.client.get("/_cluster/health")
        return {
            "status": data.get("status"),
            "number_of_nodes": data.get("number_of_nodes"),
            "active_shards": data.get("active_shards"),
            "relocating_shards": data.get("relocating_shards"),
            "initializing_shards": data.get("initializing_shards"),
            "unassigned_shards": data.get("unassigned_shards"),
            "number_of_data_nodes": data.get("number_of_data_nodes"),
            "pending_tasks": data.get("number_of_pending_tasks", 0),
            "pending_task_max_wait_ms": data.get("task_max_waiting_in_queue_millis", 0),
        }

    def index_stats(self) -> list:
        data = self.client.get("/_stats/search,indexing,store,docs,merge,query_cache,request_cache")
        indices = data.get("indices", {})
        rows = []

        for index_name, entry in sorted(indices.items()):
            if index_name.startswith(SKIPPED_INDEX_PREFIXES):
                continue

            # Docs/size/indexing count each document once, so they come from
            # primaries. Searches, caches and merges also run on replica
            # copies, so primaries alone would undercount them.
            primaries = entry.get("primaries", {})
            total = entry.get("total", {})
            search = total.get("search", {})
            indexing = primaries.get("indexing", {})
            docs = primaries.get("docs", {})
            store = primaries.get("store", {})
            merges = total.get("merges", {})
            query_cache = total.get("query_cache", {})
            request_cache = total.get("request_cache", {})

            query_total = search.get("query_total", 0)
            query_time_ms = search.get("query_time_in_millis", 0)
            indexing_total = indexing.get("index_total", 0)
            indexing_failed = indexing.get("index_failed", 0)

            qc_hit = query_cache.get("hit_count", 0)
            qc_miss = query_cache.get("miss_count", 0)
            rc_hit = request_cache.get("hit_count", 0)
            rc_miss = request_cache.get("miss_count", 0)

            avg_query_ms = query_time_ms / query_total if query_total else 0.0
            # None (not 0.0) without samples: "never used" is not "always missed".
            qc_ratio = qc_hit * 100.0 / (qc_hit + qc_miss) if (qc_hit + qc_miss) else None
            rc_ratio = rc_hit * 100.0 / (rc_hit + rc_miss) if (rc_hit + rc_miss) else None

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
        data = self.client.get("/_nodes/stats/thread_pool,jvm,breaker,fs,process,os")
        nodes = data.get("nodes", {})
        rows = []

        for node_id, node in nodes.items():
            name = node.get("name", node_id)
            search_pool = node.get("thread_pool", {}).get("search", {})
            write_pool = node.get("thread_pool", {}).get("write", {})
            heap = node.get("jvm", {}).get("mem", {})
            gc_old = node.get("jvm", {}).get("gc", {}).get("collectors", {}).get("old", {})
            process = node.get("process", {})
            open_fds = process.get("open_file_descriptors", 0)
            max_fds = process.get("max_file_descriptors", 0)
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
                "write_queue": write_pool.get("queue", 0),
                "write_rejected": write_pool.get("rejected", 0),
                "cpu_percent": node.get("os", {}).get("cpu", {}).get("percent", 0),
                "open_file_descriptors": open_fds,
                "max_file_descriptors": max_fds,
                # max is -1 where the platform doesn't report it.
                "file_descriptors_used_percent": open_fds * 100.0 / max_fds if max_fds > 0 else None,
                # Cumulative since node start, so JSON-only (no finding without a delta).
                "gc_old_collection_count": gc_old.get("collection_count", 0),
                "gc_old_collection_time_ms": gc_old.get("collection_time_in_millis", 0),
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
    def parse_watermark(value: object) -> tuple[float | None, int | None]:
        """Parse a disk watermark setting into (percent_used, min_free_bytes);
        exactly one is set for a valid value, both are None otherwise.

        OpenSearch accepts a percentage ("85%"), a ratio ("0.85") or an
        absolute size ("50gb"). An absolute size means "at least this much
        disk must stay free", so it is compared against free bytes, not used
        percent."""
        if value is None:
            return None, None
        text = str(value).strip().lower()
        if text.endswith("%"):
            try:
                return float(text[:-1]), None
            except ValueError:
                return None, None
        try:
            ratio = float(text)
        except ValueError:
            pass
        else:
            return (ratio * 100.0, None) if 0.0 <= ratio <= 1.0 else (None, None)
        match = _BYTE_SIZE_RE.match(text)
        if match and match.group(2) in _BYTE_UNITS:
            return None, int(float(match.group(1)) * _BYTE_UNITS[match.group(2)])
        return None, None

    def disk_watermarks(self) -> dict:
        """Effective disk allocation watermarks (persistent/transient override defaults).

        For each of low/high/flood_stage the result holds the percent-used
        threshold under the key itself and the absolute minimum free space
        under "<key>_free_bytes"; one of the two is None depending on how the
        watermark is configured.

        Falls back to OpenSearch's documented percent defaults only when a
        tier genuinely has no value for a key at all (e.g. an older cluster
        whose include_defaults response omits it).
        """
        data = self._cluster_settings()

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

        result: dict[str, float | int | None] = {}
        for key in ("low", "high", "flood_stage"):
            raw = pick(key)
            percent, free_bytes = (fallback_percent[key], None) if raw is None else self.parse_watermark(raw)
            result[key] = percent
            result[f"{key}_free_bytes"] = free_bytes
        return result

    def max_shards_per_node(self) -> int | None:
        """Effective cluster.max_shards_per_node, or None if unknown. A failed
        settings request yields None rather than raising: disk_watermarks()
        reads the same response and already reports that failure."""
        try:
            data = self._cluster_settings()
        except (HTTPError, URLError):
            return None
        for scope in ("transient", "persistent", "defaults"):
            value = data.get(scope, {}).get("cluster", {}).get("max_shards_per_node")
            if value is not None:
                try:
                    return int(value)
                except (TypeError, ValueError):
                    return None
        return None

    def shard_summary(self) -> dict:
        """Shard distribution from _cat/shards: total count, count per node,
        unassigned shards grouped by reason, and primaries at or above
        LARGE_SHARD_BYTES (largest first). Includes hidden indices: their
        shards count against the shard limit like any other."""
        shards = self.client.get(SHARDS_PATH)
        per_node: Counter[str] = Counter()
        unassigned_reasons: Counter[str] = Counter()
        large = []
        for shard in shards:
            if shard.get("state") == "UNASSIGNED":
                unassigned_reasons[shard.get("unassigned.reason") or "UNKNOWN"] += 1
            elif shard.get("node"):
                per_node[shard["node"]] += 1
            store_bytes = int(shard.get("store") or 0)
            if shard.get("prirep") == "p" and store_bytes >= LARGE_SHARD_BYTES:
                large.append({"index": shard.get("index"), "shard": int(shard.get("shard", 0)),
                              "node": shard.get("node"), "store_bytes": store_bytes})
        large.sort(key=lambda s: s["store_bytes"], reverse=True)
        return {
            "total": len(shards),
            "per_node": dict(sorted(per_node.items())),
            "unassigned_reasons": dict(unassigned_reasons.most_common()),
            "large_primaries": large,
            "max_shards_per_node": self.max_shards_per_node(),
        }

    def blocks(self) -> list:
        """Active cluster-wide and per-index blocks (read-only, write, ...),
        except the block every closed index carries."""
        data = self.client.get("/_cluster/state/blocks").get("blocks", {})
        rows = []
        for block_id, block in data.get("global", {}).items():
            rows.append({"index": None, "id": block_id, "description": block.get("description"),
                         "levels": block.get("levels", [])})
        for index_name, index_blocks in sorted(data.get("indices", {}).items()):
            for block_id, block in index_blocks.items():
                if block_id == CLOSED_INDEX_BLOCK_ID:
                    continue
                rows.append({"index": index_name, "id": block_id, "description": block.get("description"),
                             "levels": block.get("levels", [])})
        return rows

    def allocation_explain(self) -> dict | None:
        """Why an (arbitrary) unassigned shard can't be allocated, or None if
        there is none — the endpoint answers 400 then, which also covers the
        race of the shard being assigned since cluster_health() ran."""
        try:
            data = self.client.get("/_cluster/allocation/explain")
        except HTTPError as exc:
            if exc.code == 400:
                return None
            raise
        decider_explanation = None
        for node_decision in data.get("node_allocation_decisions", []):
            for decider in node_decision.get("deciders", []):
                if decider.get("decision") == "NO":
                    decider_explanation = f"{decider.get('decider')}: {decider.get('explanation')}"
                    break
            if decider_explanation:
                break
        return {
            "index": data.get("index"),
            "shard": data.get("shard"),
            "primary": data.get("primary"),
            "reason": data.get("unassigned_info", {}).get("reason"),
            "explanation": data.get("allocate_explanation"),
            "decider_explanation": decider_explanation,
        }

    def top_queries(self, query_type: str = "latency", limit: int = 10) -> list | None:
        """Long-running / expensive queries via the Query Insights plugin.

        Returns None if the plugin isn't installed (404) or top-N collection
        for this metric isn't enabled (400), so the report can degrade
        gracefully. Any other error propagates, notably 403: the plugin is
        there but the user lacks cluster:admin/opensearch/insights/top_queries,
        which is a misconfiguration worth reporting, not a missing plugin.

        Rows are ranked by the requested metric, matching what the plugin
        itself ranked by.
        """
        try:
            data = self.client.get(f"/_insights/top_queries?type={query_type}&verbose=true")
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

        sort_key = TOP_QUERY_SORT_KEYS[query_type]
        rows.sort(key=lambda r: r[sort_key], reverse=True)
        return rows[:limit]
