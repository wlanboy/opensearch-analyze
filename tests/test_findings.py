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
        "disk_used_percent": 10.0,
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
