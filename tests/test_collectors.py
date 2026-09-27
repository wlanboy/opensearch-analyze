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
                "top_queries-2026.09.27-04126": {"primaries": {}},
                "logs": {
                    "primaries": {
                        "search": {"query_total": 2, "query_time_in_millis": 60},
                        "indexing": {"index_total": 50, "index_failed": 2},
                        "docs": {"count": 1000},
                        "store": {"size_in_bytes": 2048},
                        "query_cache": {"hit_count": 1, "miss_count": 1},
                        "request_cache": {"hit_count": 1, "miss_count": 1},
                    },
                    "total": {
                        "search": {"query_total": 4, "query_time_in_millis": 100, "fetch_total": 1,
                                   "scroll_total": 0},
                        "indexing": {"index_total": 100, "index_failed": 2},
                        "docs": {"count": 2000},
                        "store": {"size_in_bytes": 4096},
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
    # Searches/caches run on replicas too, so they come from "total"...
    assert row["query_total"] == 4
    assert row["avg_query_ms"] == 25.0
    assert row["query_cache_hit_ratio"] == 75.0
    assert row["request_cache_hit_ratio"] == 90.0
    assert row["indexing_failed"] == 2
    # ...while docs/size/indexing count each document once (primaries).
    assert row["docs_count"] == 1000
    assert row["store_size_bytes"] == 2048
    assert row["indexing_total"] == 50


def test_index_stats_cache_ratio_is_none_without_samples():
    client = FakeClient({
        "/_stats/search,indexing,store,docs,merge,query_cache,request_cache": {
            "indices": {
                "empty": {"primaries": {}},
            },
        },
    })
    rows = OpenSearchCollector(client).index_stats()
    assert rows[0]["avg_query_ms"] == 0.0
    assert rows[0]["query_cache_hit_ratio"] is None
    assert rows[0]["request_cache_hit_ratio"] is None


def test_node_stats_computes_disk_percent_and_sorts_by_name():
    client = FakeClient({
        "/_nodes/stats/thread_pool,jvm,breaker,fs,process,os": {
            "nodes": {
                "id2": {
                    "name": "node-b",
                    "thread_pool": {"search": {"queue": 0, "rejected": 0, "active": 1},
                                    "write": {"queue": 3, "rejected": 7}},
                    "jvm": {"mem": {"heap_used_percent": 50},
                            "gc": {"collectors": {"old": {"collection_count": 2, "collection_time_in_millis": 40}}}},
                    "process": {"open_file_descriptors": 500, "max_file_descriptors": 1000},
                    "os": {"cpu": {"percent": 42}},
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
    assert rows[1]["write_queue"] == 3
    assert rows[1]["write_rejected"] == 7
    assert rows[1]["cpu_percent"] == 42
    assert rows[1]["file_descriptors_used_percent"] == 50.0
    assert rows[1]["gc_old_collection_count"] == 2
    # node-a reports no process stats: percentage unknown, not 0.
    assert rows[0]["file_descriptors_used_percent"] is None


@pytest.mark.parametrize("value,expected", [
    (None, (None, None)),
    ("85%", (85.0, None)),
    ("90.5%", (90.5, None)),
    ("0.85", (85.0, None)),
    ("1.5", (None, None)),
    ("50gb", (None, 50 * 1024 ** 3)),
    ("512MB", (None, 512 * 1024 ** 2)),
    ("1.5t", (None, int(1.5 * 1024 ** 4))),
    ("100b", (None, 100)),
    ("50xb", (None, None)),
    ("not-a-percent", (None, None)),
])
def test_parse_watermark(value, expected):
    assert OpenSearchCollector.parse_watermark(value) == expected


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
    assert result == {
        "low": 70.0, "high": 80.0, "flood_stage": 95.0,
        "low_free_bytes": None, "high_free_bytes": None, "flood_stage_free_bytes": None,
    }


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
        "low_free_bytes": None, "high_free_bytes": None, "flood_stage_free_bytes": None,
    }


def test_disk_watermarks_keeps_absolute_size_as_free_bytes_not_defaulted():
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
    # replaced by the percent-based default, but kept as min. free space.
    assert result["low"] is None
    assert result["low_free_bytes"] == 50 * 1024 ** 3
    assert result["high"] == 90.0
    assert result["high_free_bytes"] is None


def test_top_queries_returns_none_when_plugin_absent_or_metric_disabled():
    for code in (400, 404):
        client = FakeClient({
            "/_insights/top_queries?type=latency&verbose=true": make_http_error(code),
        })
        assert OpenSearchCollector(client).top_queries() is None


@pytest.mark.parametrize("code", [403, 500])
def test_top_queries_reraises_permission_and_server_errors(code):
    # 403 means the plugin is there but the user lacks permission: a
    # misconfiguration to report, not "plugin absent".
    client = FakeClient({
        "/_insights/top_queries?type=latency&verbose=true": make_http_error(code),
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


def _top_query(qid, latency, cpu, memory):
    return {"id": qid, "timestamp": 0, "indices": ["i1"], "search_type": "query_then_fetch",
            "total_shards": 1, "node_id": "n1", "source": {"query": {"match_all": {}}},
            "measurements": {"latency": {"number": latency}, "cpu": {"number": cpu},
                             "memory": {"number": memory}}}


@pytest.mark.parametrize("query_type,expected_ids", [
    ("latency", ["slow", "hungry"]),
    ("cpu", ["busy", "slow"]),
    ("memory", ["hungry", "busy"]),
])
def test_top_queries_ranks_by_requested_metric(query_type, expected_ids):
    client = FakeClient({
        f"/_insights/top_queries?type={query_type}&verbose=true": {
            "top_queries": [
                _top_query("slow", latency=900, cpu=2_000_000, memory=10),
                _top_query("busy", latency=10, cpu=9_000_000, memory=500),
                _top_query("hungry", latency=500, cpu=1_000_000, memory=9000),
            ],
        },
    })
    rows = OpenSearchCollector(client).top_queries(query_type, limit=2)
    assert rows is not None
    assert [r["id"] for r in rows] == expected_ids


def test_cluster_health_includes_pending_tasks_and_data_nodes():
    client = FakeClient({"/_cluster/health": {
        "status": "green", "number_of_data_nodes": 2, "number_of_pending_tasks": 3,
        "task_max_waiting_in_queue_millis": 1500,
    }})
    result = OpenSearchCollector(client).cluster_health()
    assert result["number_of_data_nodes"] == 2
    assert result["pending_tasks"] == 3
    assert result["pending_task_max_wait_ms"] == 1500


SHARDS_PATH = "/_cat/shards?format=json&bytes=b&h=index,shard,prirep,state,store,node,unassigned.reason"
SETTINGS_PATH = "/_cluster/settings?include_defaults=true"


def test_shard_summary():
    gb = 1024 ** 3
    client = FakeClient({
        SHARDS_PATH: [
            {"index": "big", "shard": "0", "prirep": "p", "state": "STARTED", "store": str(60 * gb), "node": "n2"},
            {"index": "big", "shard": "0", "prirep": "r", "state": "STARTED", "store": str(60 * gb), "node": "n1"},
            {"index": ".hidden", "shard": "0", "prirep": "p", "state": "STARTED", "store": "208", "node": "n1"},
            {"index": "logs", "shard": "0", "prirep": "r", "state": "UNASSIGNED", "store": None, "node": None,
             "unassigned.reason": "NODE_LEFT"},
            {"index": "logs", "shard": "1", "prirep": "r", "state": "UNASSIGNED", "store": None, "node": None,
             "unassigned.reason": "NODE_LEFT"},
            {"index": "new", "shard": "0", "prirep": "p", "state": "UNASSIGNED", "store": None, "node": None,
             "unassigned.reason": "INDEX_CREATED"},
        ],
        SETTINGS_PATH: {"persistent": {"cluster": {"max_shards_per_node": "500"}},
                        "defaults": {"cluster": {"max_shards_per_node": "1000"}}},
    })
    result = OpenSearchCollector(client).shard_summary()
    assert result["total"] == 6
    assert result["per_node"] == {"n1": 2, "n2": 1}
    assert result["unassigned_reasons"] == {"NODE_LEFT": 2, "INDEX_CREATED": 1}
    # Only the primary counts; the replica copy would list the same data twice.
    assert result["large_primaries"] == [{"index": "big", "shard": 0, "node": "n2", "store_bytes": 60 * gb}]
    assert result["max_shards_per_node"] == 500


def test_max_shards_per_node_none_when_settings_fail():
    client = FakeClient({SETTINGS_PATH: make_http_error(403)})
    assert OpenSearchCollector(client).max_shards_per_node() is None


def test_cluster_settings_fetched_once_per_collector():
    calls = []

    class CountingClient(FakeClient):
        def get(self, path):
            calls.append(path)
            return super().get(path)

    client = CountingClient({SETTINGS_PATH: {"defaults": {"cluster": {"max_shards_per_node": "1000"}}}})
    collector = OpenSearchCollector(client)
    collector.disk_watermarks()
    collector.max_shards_per_node()
    assert calls == [SETTINGS_PATH]


def test_blocks_lists_global_and_index_blocks_but_not_closed_indices():
    client = FakeClient({"/_cluster/state/blocks": {"blocks": {
        "global": {"5": {"description": "cluster read-only (api)", "levels": ["write", "metadata_write"]}},
        "indices": {
            "logs": {"12": {"description": "flood-stage", "levels": ["write", "metadata_write"]}},
            "archived": {"4": {"description": "index closed", "levels": ["read", "write"]}},
        },
    }}})
    assert OpenSearchCollector(client).blocks() == [
        {"index": None, "id": "5", "description": "cluster read-only (api)", "levels": ["write", "metadata_write"]},
        {"index": "logs", "id": "12", "description": "flood-stage", "levels": ["write", "metadata_write"]},
    ]


def test_blocks_empty():
    client = FakeClient({"/_cluster/state/blocks": {"cluster_name": "c", "blocks": {}}})
    assert OpenSearchCollector(client).blocks() == []


def test_allocation_explain_picks_first_no_decider():
    client = FakeClient({"/_cluster/allocation/explain": {
        "index": "logs", "shard": 0, "primary": False,
        "unassigned_info": {"reason": "INDEX_CREATED"},
        "allocate_explanation": "cannot allocate because allocation is not permitted to any of the nodes",
        "node_allocation_decisions": [
            {"node_name": "n1", "deciders": [
                {"decider": "filter", "decision": "YES", "explanation": "ok"},
                {"decider": "same_shard", "decision": "NO", "explanation": "a copy is already here"},
            ]},
        ],
    }})
    assert OpenSearchCollector(client).allocation_explain() == {
        "index": "logs", "shard": 0, "primary": False, "reason": "INDEX_CREATED",
        "explanation": "cannot allocate because allocation is not permitted to any of the nodes",
        "decider_explanation": "same_shard: a copy is already here",
    }


def test_allocation_explain_none_when_nothing_unassigned():
    client = FakeClient({"/_cluster/allocation/explain": make_http_error(400)})
    assert OpenSearchCollector(client).allocation_explain() is None
