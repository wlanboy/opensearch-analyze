"""Turns collected cluster/index/node data into human-readable warning strings."""

from .constants import (
    CACHE_HIT_RATIO_WARN,
    CACHE_SAMPLE_MIN,
    CPU_WARN_PERCENT,
    FD_WARN_PERCENT,
    HEAP_WARN_PERCENT,
    LARGE_SHARD_BYTES,
    PENDING_TASK_WAIT_WARN_MS,
    PENDING_TASKS_WARN,
    SHARD_LIMIT_WARN_PERCENT,
    SLOW_QUERY_WARN_MS,
)
from .formatting import Formatter
from .i18n import Translator

# Checked most severe first; only the first tier a node reaches is reported.
_DISK_TIERS = (("flood_stage", "finding_disk_flood"), ("high", "finding_disk_high"), ("low", "finding_disk_low"))


class FindingsBuilder:
    @staticmethod
    def format_watermark(lang: str, watermarks: dict, key: str) -> str | None:
        """Display form of one watermark: "95%" or "min. 50.0GB free", None if unknown."""
        percent = watermarks.get(key)
        if percent is not None:
            return f"{percent:.0f}%"
        free_bytes = watermarks.get(f"{key}_free_bytes")
        if free_bytes is not None:
            return Translator.t(lang, "watermark_min_free", size=Formatter.format_bytes(free_bytes))
        return None

    @staticmethod
    def watermark_reached(node: dict, watermarks: dict, key: str) -> bool:
        percent = watermarks.get(key)
        if percent is not None:
            return node["disk_used_percent"] >= percent
        free_bytes = watermarks.get(f"{key}_free_bytes")
        if free_bytes is not None:
            # A node without fs stats reports 0 total bytes; don't flag it.
            return node["disk_total_bytes"] > 0 and node["disk_available_bytes"] <= free_bytes
        return False

    @staticmethod
    def shard_findings(lang: str, cluster: dict, shards: dict) -> list:
        findings = []
        limit_per_node = shards.get("max_shards_per_node")
        data_nodes = cluster.get("number_of_data_nodes")
        if limit_per_node and data_nodes:
            limit = limit_per_node * data_nodes
            pct = shards["total"] * 100.0 / limit
            if pct >= SHARD_LIMIT_WARN_PERCENT:
                findings.append(Translator.t(lang, "finding_shard_limit", total=shards["total"], pct=f"{pct:.0f}",
                                             limit=limit, per_node=limit_per_node, nodes=data_nodes))

        by_index: dict[str, list] = {}
        for shard in shards.get("large_primaries", []):
            by_index.setdefault(shard["index"], []).append(shard)
        for index_name, large in by_index.items():
            findings.append(Translator.t(
                lang, "finding_large_shards", index=index_name, n=len(large),
                limit=Formatter.format_bytes(LARGE_SHARD_BYTES),
                largest=Formatter.format_bytes(max(s["store_bytes"] for s in large)),
            ))
        return findings

    @staticmethod
    def build(lang: str, cluster: dict, indices: list, nodes: list, top_queries: list | None = None,
              watermarks: dict | None = None, shards: dict | None = None, blocks: list | None = None,
              allocation: dict | None = None) -> list:
        findings = []
        watermarks = watermarks or {}

        if cluster.get("status") in ("yellow", "red"):
            findings.append(Translator.t(lang, "finding_cluster_status", status=cluster["status"].upper()))
        if cluster.get("unassigned_shards", 0) > 0:
            reasons = (shards or {}).get("unassigned_reasons")
            if reasons:
                findings.append(Translator.t(
                    lang, "finding_unassigned_shards_reasons", n=cluster["unassigned_shards"],
                    reasons=", ".join(f"{reason} ×{count}" for reason, count in reasons.items()),
                ))
            else:
                findings.append(Translator.t(lang, "finding_unassigned_shards", n=cluster["unassigned_shards"]))
        if allocation:
            copy = Translator.t(lang, "shard_primary" if allocation.get("primary") else "shard_replica")
            finding = Translator.t(lang, "finding_allocation_explain", index=allocation.get("index"),
                                   shard=allocation.get("shard"), copy=copy,
                                   explanation=allocation.get("explanation"))
            if allocation.get("decider_explanation"):
                finding += f" — {allocation['decider_explanation']}"
            findings.append(finding)
        pending = cluster.get("pending_tasks") or 0
        max_wait = cluster.get("pending_task_max_wait_ms") or 0
        if pending >= PENDING_TASKS_WARN or max_wait >= PENDING_TASK_WAIT_WARN_MS:
            findings.append(Translator.t(lang, "finding_pending_tasks", n=pending,
                                         wait=Formatter.format_ms(max_wait)))

        for block in blocks or []:
            levels = ", ".join(block["levels"])
            if block["index"] is None:
                findings.append(Translator.t(lang, "finding_block_cluster", description=block["description"],
                                             levels=levels))
            else:
                findings.append(Translator.t(lang, "finding_block_index", index=block["index"],
                                             description=block["description"], levels=levels))

        if shards:
            findings.extend(FindingsBuilder.shard_findings(lang, cluster, shards))

        for idx in indices:
            if idx["indexing_failed"] > 0:
                findings.append(
                    Translator.t(lang, "finding_indexing_failed", index=idx["index"], n=idx["indexing_failed"])
                )
            qc_ratio = idx["query_cache_hit_ratio"]
            if idx["query_cache_samples"] >= CACHE_SAMPLE_MIN and qc_ratio is not None \
                    and qc_ratio < CACHE_HIT_RATIO_WARN:
                findings.append(
                    Translator.t(lang, "finding_low_query_cache", index=idx["index"],
                                 pct=f"{qc_ratio:.1f}")
                )

        for node in nodes:
            if node["heap_used_percent"] >= HEAP_WARN_PERCENT:
                findings.append(Translator.t(lang, "finding_heap", node=node["node"], pct=node["heap_used_percent"]))
            if node["search_rejected"] > 0:
                findings.append(
                    Translator.t(lang, "finding_search_rejected", node=node["node"], n=node["search_rejected"])
                )
            if node["write_rejected"] > 0:
                findings.append(
                    Translator.t(lang, "finding_write_rejected", node=node["node"], n=node["write_rejected"])
                )
            if node["cpu_percent"] >= CPU_WARN_PERCENT:
                findings.append(Translator.t(lang, "finding_cpu", node=node["node"], pct=node["cpu_percent"]))
            fd_pct = node["file_descriptors_used_percent"]
            if fd_pct is not None and fd_pct >= FD_WARN_PERCENT:
                findings.append(Translator.t(lang, "finding_file_descriptors", node=node["node"],
                                             pct=f"{fd_pct:.0f}", open=node["open_file_descriptors"],
                                             max=node["max_file_descriptors"]))
            tripped = (
                node["breaker_parent_tripped"] + node["breaker_fielddata_tripped"] + node["breaker_request_tripped"]
            )
            if tripped > 0:
                findings.append(Translator.t(lang, "finding_breaker_tripped", node=node["node"], n=tripped))

            for key, message_key in _DISK_TIERS:
                if FindingsBuilder.watermark_reached(node, watermarks, key):
                    findings.append(Translator.t(
                        lang, message_key, node=node["node"], pct=f"{node['disk_used_percent']:.1f}",
                        free=Formatter.format_bytes(node["disk_available_bytes"]),
                        wm=FindingsBuilder.format_watermark(lang, watermarks, key),
                    ))
                    break

        if top_queries:
            slow = [q for q in top_queries if q["latency_ms"] >= SLOW_QUERY_WARN_MS]
            if slow:
                worst = max(slow, key=lambda q: q["latency_ms"])
                findings.append(Translator.t(
                    lang, "finding_slow_queries",
                    count=len(slow), word=Translator.slow_query_word(lang, len(slow)),
                    ms=SLOW_QUERY_WARN_MS, worst_ms=worst["latency_ms"], indices=worst["indices"],
                ))

        return findings
