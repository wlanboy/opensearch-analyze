"""Turns collected cluster/index/node data into human-readable warning strings."""

from .constants import CACHE_HIT_RATIO_WARN, CACHE_SAMPLE_MIN, HEAP_WARN_PERCENT, SLOW_QUERY_WARN_MS
from .i18n import Translator


class FindingsBuilder:
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
            if idx["query_cache_samples"] >= CACHE_SAMPLE_MIN and idx["query_cache_hit_ratio"] < CACHE_HIT_RATIO_WARN:
                findings.append(
                    Translator.t(lang, "finding_low_query_cache", index=idx["index"],
                                 pct=f"{idx['query_cache_hit_ratio']:.1f}")
                )

        flood_wm = watermarks.get("flood_stage")
        high_wm = watermarks.get("high")
        low_wm = watermarks.get("low")

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

            disk_pct = node["disk_used_percent"]
            if flood_wm is not None and disk_pct >= flood_wm:
                findings.append(
                    Translator.t(lang, "finding_disk_flood", node=node["node"], pct=f"{disk_pct:.1f}",
                                 wm=f"{flood_wm:.0f}")
                )
            elif high_wm is not None and disk_pct >= high_wm:
                findings.append(
                    Translator.t(lang, "finding_disk_high", node=node["node"], pct=f"{disk_pct:.1f}",
                                 wm=f"{high_wm:.0f}")
                )
            elif low_wm is not None and disk_pct >= low_wm:
                findings.append(
                    Translator.t(lang, "finding_disk_low", node=node["node"], pct=f"{disk_pct:.1f}",
                                 wm=f"{low_wm:.0f}")
                )

        if top_queries:
            slow = [q for q in top_queries if q["latency_ms"] >= SLOW_QUERY_WARN_MS]
            if slow:
                worst = slow[0]
                findings.append(Translator.t(
                    lang, "finding_slow_queries",
                    count=len(slow), word=Translator.slow_query_word(lang, len(slow)),
                    ms=SLOW_QUERY_WARN_MS, worst_ms=worst["latency_ms"], indices=worst["indices"],
                ))

        return findings
