from urllib.error import HTTPError

import pytest
from fakes import FakeClient, make_http_error

from opensearch_analyze.collectors import OpenSearchCollector
from opensearch_analyze.constants import (
    DISK_WATERMARK_DEFAULT_FLOOD,
    DISK_WATERMARK_DEFAULT_HIGH,
    DISK_WATERMARK_DEFAULT_LOW,
)


def test_cluster_health():
    client = FakeClient({
        "/_cluster/health": {
            "status": "green", "number_of_nodes": 2, "active_shards": 10,
            "relocating_shards": 0, "initializing_shards": 0, "unassigned_shards": 0,
        },
    })
    result = OpenSearchCollector(client).cluster_health()
    assert result["status"] == "green"
    assert result["number_of_nodes"] == 2


def test_index_stats_skips_dot_indices_and_computes_ratios():
    client = FakeClient({
        "/_stats/search,indexing,store,docs,merge,query_cache,request_cache": {
            "indices": {
                ".kibana": {"primaries": {}},
                "logs": {
                    "primaries": {
                        "search": {"query_total": 4, "query_time_in_millis": 100, "fetch_total": 1,
                                   "scroll_total": 0},
                        "indexing": {"index_total": 50, "index_failed": 2},
                        "docs": {"count": 1000},
                        "store": {"size_in_bytes": 2048},
                        "merges": {"current": 0},
                        "query_cache": {"hit_count": 3, "miss_count": 1},
                        "request_cache": {"hit_count": 9, "miss_count": 1},
                    },
                },
            },
        },
    })
    rows = OpenSearchCollector(client).index_stats()
    assert len(rows) == 1
    row = rows[0]
    assert row["index"] == "logs"
    assert row["avg_query_ms"] == 25.0
    assert row["query_cache_hit_ratio"] == 75.0
    assert row["request_cache_hit_ratio"] == 90.0
    assert row["indexing_failed"] == 2


def test_index_stats_handles_zero_totals_without_division_error():
    client = FakeClient({
        "/_stats/search,indexing,store,docs,merge,query_cache,request_cache": {
            "indices": {
                "empty": {"primaries": {}},
            },
        },
    })
    rows = OpenSearchCollector(client).index_stats()
    assert rows[0]["avg_query_ms"] == 0.0
    assert rows[0]["query_cache_hit_ratio"] == 0.0
    assert rows[0]["request_cache_hit_ratio"] == 0.0


def test_node_stats_computes_disk_percent_and_sorts_by_name():
    client = FakeClient({
        "/_nodes/stats/thread_pool,jvm,breaker,fs": {
            "nodes": {
                "id2": {
                    "name": "node-b",
                    "thread_pool": {"search": {"queue": 0, "rejected": 0, "active": 1}},
                    "jvm": {"mem": {"heap_used_percent": 50}},
                    "breakers": {},
                    "fs": {"total": {"total_in_bytes": 1000, "available_in_bytes": 400}},
                },
                "id1": {
                    "name": "node-a",
                    "thread_pool": {"search": {"queue": 0, "rejected": 0, "active": 0}},
                    "jvm": {"mem": {"heap_used_percent": 10}},
                    "breakers": {},
                    "fs": {"total": {"total_in_bytes": 1000, "available_in_bytes": 1000}},
                },
            },
        },
    })
    rows = OpenSearchCollector(client).node_stats()
    assert [r["node"] for r in rows] == ["node-a", "node-b"]
    assert rows[1]["disk_used_percent"] == 60.0
    assert rows[0]["disk_used_percent"] == 0.0


@pytest.mark.parametrize("value,expected", [
    (None, None),
    ("85%", 85.0),
    ("90.5%", 90.5),
    ("50gb", None),
    ("not-a-percent", None),
])
def test_parse_watermark_percent(value, expected):
    assert OpenSearchCollector.parse_watermark_percent(value) == expected


def test_disk_watermarks_uses_transient_over_persistent_over_defaults():
    client = FakeClient({
        "/_cluster/settings?include_defaults=true": {
            "transient": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {"low": "70%"}}}}}},
            "persistent": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {
                "low": "75%", "high": "80%",
            }}}}}},
            "defaults": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {
                "low": "85%", "high": "90%", "flood_stage": "95%",
            }}}}}},
        },
    })
    result = OpenSearchCollector(client).disk_watermarks()
    assert result == {"low": 70.0, "high": 80.0, "flood_stage": 95.0}


def test_disk_watermarks_falls_back_to_documented_defaults_when_missing():
    client = FakeClient({
        "/_cluster/settings?include_defaults=true": {
            "transient": {}, "persistent": {}, "defaults": {},
        },
    })
    result = OpenSearchCollector(client).disk_watermarks()
    assert result == {
        "low": DISK_WATERMARK_DEFAULT_LOW,
        "high": DISK_WATERMARK_DEFAULT_HIGH,
        "flood_stage": DISK_WATERMARK_DEFAULT_FLOOD,
    }


def test_disk_watermarks_leaves_absolute_size_unresolved_not_defaulted():
    client = FakeClient({
        "/_cluster/settings?include_defaults=true": {
            "transient": {}, "persistent": {},
            "defaults": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {
                "low": "50gb", "high": "90%", "flood_stage": "95%",
            }}}}}},
        },
    })
    result = OpenSearchCollector(client).disk_watermarks()
    # Configured explicitly as an absolute size: must NOT be silently
    # replaced by the percent-based default (that would misrepresent an
    # intentional admin setting).
    assert result["low"] is None
    assert result["high"] == 90.0


def test_top_queries_returns_none_when_plugin_absent():
    for code in (400, 403, 404):
        client = FakeClient({
            "/_insights/top_queries?type=latency&verbose=true": make_http_error(code),
        })
        assert OpenSearchCollector(client).top_queries() is None


def test_top_queries_reraises_unexpected_errors():
    client = FakeClient({
        "/_insights/top_queries?type=latency&verbose=true": make_http_error(500),
    })
    with pytest.raises(HTTPError):
        OpenSearchCollector(client).top_queries()


def test_top_queries_sorts_by_latency_desc_and_applies_limit():
    client = FakeClient({
        "/_insights/top_queries?type=latency&verbose=true": {
            "top_queries": [
                {"id": "a", "timestamp": 0, "measurements": {"latency": {"number": 100}},
                 "indices": ["i1"], "search_type": "query_then_fetch", "total_shards": 1,
                 "node_id": "n1", "source": {"query": {"match_all": {}}}},
                {"id": "b", "timestamp": 0, "measurements": {"latency": {"number": 900}},
                 "indices": ["i2"], "search_type": "query_then_fetch", "total_shards": 1,
                 "node_id": "n1", "source": {"query": {"match_all": {}}}},
            ],
        },
    })
    rows = OpenSearchCollector(client).top_queries(limit=1)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["id"] == "b"
    assert rows[0]["latency_ms"] == 900
