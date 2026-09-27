"""Turns collected cluster/index/node data into human-readable warning strings."""

from .constants import (
    CACHE_HIT_RATIO_WARN,
    CACHE_SAMPLE_MIN,
    HEAP_WARN_PERCENT,
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
    def build(lang: str, cluster: dict, indices: list, nodes: list, top_queries: list | None = None,
              watermarks: dict | None = None) -> list:
        findings = []
        watermarks = watermarks or {}

        if cluster.get("status") in ("yellow", "red"):
            findings.append(Translator.t(lang, "finding_cluster_status", status=cluster["status"].upper()))
        if cluster.get("unassigned_shards", 0) > 0:
            findings.append(Translator.t(lang, "finding_unassigned_shards", n=cluster["unassigned_shards"]))

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
