import pytest

from opensearch_analyze.findings import FindingsBuilder


def _cluster(status="green", unassigned=0):
    return {"status": status, "unassigned_shards": unassigned}


def _index_row(**overrides):
    row = {
        "index": "logs", "indexing_failed": 0, "query_cache_samples": 0,
        "query_cache_hit_ratio": 100.0,
    }
    row.update(overrides)
    return row


def _node_row(**overrides):
    row = {
        "node": "node-a", "heap_used_percent": 10, "search_rejected": 0,
        "breaker_parent_tripped": 0, "breaker_fielddata_tripped": 0, "breaker_request_tripped": 0,
        "disk_used_percent": 10.0, "disk_available_bytes": 900 * 1024 ** 3,
        "disk_total_bytes": 1000 * 1024 ** 3,
    }
    row.update(overrides)
    return row


def test_empty_when_healthy():
    findings = FindingsBuilder.build("en", _cluster(), [_index_row()], [_node_row()])
    assert findings == []


def test_cluster_status_and_unassigned_shards():
    findings = FindingsBuilder.build("en", _cluster(status="red", unassigned=3), [], [])
    assert "cluster status is RED" in findings
    assert "3 unassigned shard(s)" in findings


def test_indexing_failed_and_low_cache_ratio():
    findings = FindingsBuilder.build("en", _cluster(), [
        _index_row(indexing_failed=5),
        _index_row(index="metrics", query_cache_samples=200, query_cache_hit_ratio=10.0),
    ], [])
    assert "index 'logs': 5 failed indexing operation(s)" in findings
    assert "index 'metrics': low query cache hit ratio (10.0%)" in findings


def test_heap_search_rejected_and_breaker():
    findings = FindingsBuilder.build("en", _cluster(), [], [
        _node_row(heap_used_percent=90, search_rejected=2, breaker_parent_tripped=1),
    ])
    assert "node 'node-a': JVM heap at 90%" in findings
    assert "node 'node-a': 2 rejected search task(s)" in findings
    assert "node 'node-a': circuit breaker tripped 1 time(s)" in findings


@pytest.mark.parametrize("disk_pct,expected_substr", [
    (96.0, "flood-stage watermark"),
    (91.0, "high watermark"),
    (86.0, "low watermark"),
])
def test_disk_watermark_tiers_are_mutually_exclusive(disk_pct, expected_substr):
    watermarks = {"low": 85.0, "high": 90.0, "flood_stage": 95.0}
    findings = FindingsBuilder.build("en", _cluster(), [], [_node_row(disk_used_percent=disk_pct)],
                                      watermarks=watermarks)
    disk_findings = [f for f in findings if "watermark" in f]
    assert len(disk_findings) == 1
    assert expected_substr in disk_findings[0]


def test_slow_queries_singular_vs_plural():
    one_slow = [{"latency_ms": 1500, "indices": "logs"}]
    findings = FindingsBuilder.build("en", _cluster(), [], [], top_queries=one_slow)
    assert "1 long-running query >= 1000ms (worst: 1500ms on logs)" in findings

    two_slow = [{"latency_ms": 1500, "indices": "logs"}, {"latency_ms": 2000, "indices": "metrics"}]
    findings = FindingsBuilder.build("en", _cluster(), [], [], top_queries=two_slow)
    assert any("2 long-running queries >=" in f for f in findings)


def test_slow_queries_german():
    two_slow = [{"latency_ms": 1500, "indices": "logs"}, {"latency_ms": 2000, "indices": "metrics"}]
    findings = FindingsBuilder.build("de", _cluster(), [], [], top_queries=two_slow)
    assert any("2 lang laufende Abfragen >=" in f for f in findings)


def test_cache_ratio_none_is_not_a_finding():
    findings = FindingsBuilder.build("en", _cluster(), [
        _index_row(query_cache_samples=200, query_cache_hit_ratio=None),
    ], [])
    assert findings == []


def test_disk_watermark_finding_text():
    watermarks = {"low": 85.0, "high": 90.0, "flood_stage": 95.0}
    node = _node_row(disk_used_percent=96.0, disk_available_bytes=40 * 1024 ** 3)
    findings = FindingsBuilder.build("en", _cluster(), [], [node], watermarks=watermarks)
    assert findings == [(
        "node 'node-a': disk at 96.0% (40.0GB free) reached flood-stage watermark (95%) — indices on this "
        "node are likely forced read-only"
    )]


@pytest.mark.parametrize("free_gb,expected_substr", [
    (5, "flood-stage watermark (min. 10.0GB free)"),
    (15, "high watermark (min. 20.0GB free)"),
    (25, "low watermark (min. 30.0GB free)"),
    (35, None),
])
def test_absolute_disk_watermarks_compare_free_bytes(free_gb, expected_substr):
    gb = 1024 ** 3
    watermarks = {
        "low": None, "high": None, "flood_stage": None,
        "low_free_bytes": 30 * gb, "high_free_bytes": 20 * gb, "flood_stage_free_bytes": 10 * gb,
    }
    # Low percent usage on a big disk: only the free-bytes comparison can fire.
    node = _node_row(disk_used_percent=50.0, disk_available_bytes=free_gb * gb, disk_total_bytes=1000 * gb)
    findings = FindingsBuilder.build("en", _cluster(), [], [node], watermarks=watermarks)
    disk_findings = [f for f in findings if "watermark" in f]
    if expected_substr is None:
        assert disk_findings == []
    else:
        assert len(disk_findings) == 1
        assert expected_substr in disk_findings[0]


def test_absolute_disk_watermark_ignores_node_without_fs_stats():
    watermarks = {"flood_stage": None, "flood_stage_free_bytes": 10 * 1024 ** 3}
    node = _node_row(disk_used_percent=0.0, disk_available_bytes=0, disk_total_bytes=0)
    assert FindingsBuilder.build("en", _cluster(), [], [node], watermarks=watermarks) == []


def test_slow_queries_worst_is_max_latency_regardless_of_order():
    # Rows ranked by cpu/memory aren't in latency order.
    rows = [{"latency_ms": 1500, "indices": "logs"}, {"latency_ms": 3000, "indices": "metrics"}]
    findings = FindingsBuilder.build("en", _cluster(), [], [], top_queries=rows)
    assert "2 long-running queries >= 1000ms (worst: 3000ms on metrics)" in findings
