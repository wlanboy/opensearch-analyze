"""Unit tests for the opensearch_analyze package.

All collectors are exercised against a FakeClient (no real network calls);
HTTP/URL errors are constructed directly to test the error-handling and
graceful-degradation paths.
"""

import email.message
import io
import ssl
from urllib.error import HTTPError, URLError

import pytest

import opensearch_analyze as oa

# ---------------------------------------------------------------------------
# Test doubles
# ---------------------------------------------------------------------------

class FakeClient:
    """Stands in for OpenSearchClient: .get(path) returns canned data or
    raises a canned exception, keyed by path."""

    def __init__(self, responses: dict, host: str = "http://fake:9200"):
        self.responses = responses
        self.host = host

    def get(self, path: str):
        value = self.responses[path]
        if isinstance(value, BaseException):
            raise value
        return value


def make_http_error(code: int, url: str = "http://fake:9200/x", body: bytes = b"boom") -> HTTPError:
    return HTTPError(url=url, code=code, msg="error", hdrs=email.message.Message(), fp=io.BytesIO(body))


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("n,expected", [
    (0, "0.0B"),
    (512, "512.0B"),
    (1024, "1.0KB"),
    (1024 * 1024, "1.0MB"),
    (1024 ** 3, "1.0GB"),
    (1024 ** 4, "1.0TB"),
    (1024 ** 5, "1.0PB"),
])
def test_format_bytes(n, expected):
    assert oa.format_bytes(n) == expected


@pytest.mark.parametrize("n,expected", [
    (0, "0ms"),
    (999, "999ms"),
    (1000, "1.00s"),
    (2500, "2.50s"),
])
def test_format_ms(n, expected):
    assert oa.format_ms(n) == expected


def test_format_timestamp_ms():
    # 0 ms epoch formats as a plain HH:MM:SS string, locale/timezone-dependent
    # but always well-formed.
    result = oa.format_timestamp_ms(0)
    assert len(result) == 8
    assert result.count(":") == 2


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

def test_T_formats_with_kwargs():
    assert oa.T("en", "finding_unassigned_shards", n=3) == "3 unassigned shard(s)"
    assert oa.T("de", "finding_unassigned_shards", n=3) == "3 nicht zugewiesene(r) Shard(s)"


def test_T_falls_back_to_english_for_unknown_lang():
    assert oa.T("fr", "no_issues") == oa.T("en", "no_issues")


def test_T_headers_returns_list():
    headers = oa.T_HEADERS("en", "headers_cluster")
    assert isinstance(headers, list)
    assert "status" in headers


def test_section_label_known_and_unknown_lang():
    assert oa.section_label("de", "nodes") == "Knoten"
    assert oa.section_label("fr", "nodes") == "nodes"


@pytest.mark.parametrize("lang,count,expected", [
    ("en", 1, "long-running query"),
    ("en", 2, "long-running queries"),
    ("de", 1, "lang laufende Abfrage"),
    ("de", 2, "lang laufende Abfragen"),
])
def test_slow_query_word(lang, count, expected):
    assert oa._slow_query_word(lang, count) == expected


# ---------------------------------------------------------------------------
# OpenSearchClient auth / TLS wiring
# ---------------------------------------------------------------------------

def test_client_basic_auth_header():
    client = oa.OpenSearchClient("http://x", username="u", password="p")
    assert client._auth_header == "Basic dTpw"  # base64("u:p")


def test_client_api_key_with_colon_is_base64_encoded():
    client = oa.OpenSearchClient("http://x", api_key="id:secret")
    assert client._auth_header == "ApiKey aWQ6c2VjcmV0"  # base64("id:secret")


def test_client_api_key_without_colon_is_passed_through():
    client = oa.OpenSearchClient("http://x", api_key="already-encoded-token")
    assert client._auth_header == "ApiKey already-encoded-token"


def test_client_bearer_token_header():
    client = oa.OpenSearchClient("http://x", bearer_token="jwt.token.here")
    assert client._auth_header == "Bearer jwt.token.here"


def test_client_basic_auth_takes_priority_over_others():
    client = oa.OpenSearchClient("http://x", username="u", password="p",
                                  api_key="id:secret", bearer_token="jwt")
    assert client._auth_header == "Basic dTpw"


def test_client_no_auth_header_without_credentials():
    client = oa.OpenSearchClient("http://x")
    assert client._auth_header is None


def test_client_no_tls_context_for_plain_http():
    client = oa.OpenSearchClient("http://x", verify_tls=False)
    assert client._ssl_context is None


def test_client_insecure_https_uses_public_ssl_api_and_disables_verification():
    client = oa.OpenSearchClient("https://x", verify_tls=False)
    assert isinstance(client._ssl_context, ssl.SSLContext)
    assert client._ssl_context.check_hostname is False
    assert client._ssl_context.verify_mode == ssl.CERT_NONE


def test_client_verified_https_has_no_special_context():
    client = oa.OpenSearchClient("https://x", verify_tls=True)
    assert client._ssl_context is None


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def test_collect_cluster_health():
    client = FakeClient({
        "/_cluster/health": {
            "status": "green", "number_of_nodes": 2, "active_shards": 10,
            "relocating_shards": 0, "initializing_shards": 0, "unassigned_shards": 0,
        },
    })
    result = oa.collect_cluster_health(client)
    assert result["status"] == "green"
    assert result["number_of_nodes"] == 2


def test_collect_index_stats_skips_dot_indices_and_computes_ratios():
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
    rows = oa.collect_index_stats(client)
    assert len(rows) == 1
    row = rows[0]
    assert row["index"] == "logs"
    assert row["avg_query_ms"] == 25.0
    assert row["query_cache_hit_ratio"] == 75.0
    assert row["request_cache_hit_ratio"] == 90.0
    assert row["indexing_failed"] == 2


def test_collect_index_stats_handles_zero_totals_without_division_error():
    client = FakeClient({
        "/_stats/search,indexing,store,docs,merge,query_cache,request_cache": {
            "indices": {
                "empty": {"primaries": {}},
            },
        },
    })
    rows = oa.collect_index_stats(client)
    assert rows[0]["avg_query_ms"] == 0.0
    assert rows[0]["query_cache_hit_ratio"] == 0.0
    assert rows[0]["request_cache_hit_ratio"] == 0.0


def test_collect_node_stats_computes_disk_percent_and_sorts_by_name():
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
    rows = oa.collect_node_stats(client)
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
    assert oa.parse_watermark_percent(value) == expected


def test_collect_disk_watermarks_uses_transient_over_persistent_over_defaults():
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
    result = oa.collect_disk_watermarks(client)
    assert result == {"low": 70.0, "high": 80.0, "flood_stage": 95.0}


def test_collect_disk_watermarks_falls_back_to_documented_defaults_when_missing():
    client = FakeClient({
        "/_cluster/settings?include_defaults=true": {
            "transient": {}, "persistent": {}, "defaults": {},
        },
    })
    result = oa.collect_disk_watermarks(client)
    assert result == {
        "low": oa.DISK_WATERMARK_DEFAULT_LOW,
        "high": oa.DISK_WATERMARK_DEFAULT_HIGH,
        "flood_stage": oa.DISK_WATERMARK_DEFAULT_FLOOD,
    }


def test_collect_disk_watermarks_leaves_absolute_size_unresolved_not_defaulted():
    client = FakeClient({
        "/_cluster/settings?include_defaults=true": {
            "transient": {}, "persistent": {},
            "defaults": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {
                "low": "50gb", "high": "90%", "flood_stage": "95%",
            }}}}}},
        },
    })
    result = oa.collect_disk_watermarks(client)
    # Configured explicitly as an absolute size: must NOT be silently
    # replaced by the percent-based default (that would misrepresent an
    # intentional admin setting).
    assert result["low"] is None
    assert result["high"] == 90.0


def test_collect_top_queries_returns_none_when_plugin_absent():
    for code in (400, 403, 404):
        client = FakeClient({
            "/_insights/top_queries?type=latency&verbose=true": make_http_error(code),
        })
        assert oa.collect_top_queries(client) is None


def test_collect_top_queries_reraises_unexpected_errors():
    client = FakeClient({
        "/_insights/top_queries?type=latency&verbose=true": make_http_error(500),
    })
    with pytest.raises(HTTPError):
        oa.collect_top_queries(client)


def test_collect_top_queries_sorts_by_latency_desc_and_applies_limit():
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
    rows = oa.collect_top_queries(client, limit=1)
    assert rows is not None
    assert len(rows) == 1
    assert rows[0]["id"] == "b"
    assert rows[0]["latency_ms"] == 900


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

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


def test_build_findings_empty_when_healthy():
    findings = oa.build_findings("en", _cluster(), [_index_row()], [_node_row()])
    assert findings == []


def test_build_findings_cluster_status_and_unassigned_shards():
    findings = oa.build_findings("en", _cluster(status="red", unassigned=3), [], [])
    assert "cluster status is RED" in findings
    assert "3 unassigned shard(s)" in findings


def test_build_findings_indexing_failed_and_low_cache_ratio():
    findings = oa.build_findings("en", _cluster(), [
        _index_row(indexing_failed=5),
        _index_row(index="metrics", query_cache_samples=200, query_cache_hit_ratio=10.0),
    ], [])
    assert "index 'logs': 5 failed indexing operation(s)" in findings
    assert "index 'metrics': low query cache hit ratio (10.0%)" in findings


def test_build_findings_heap_search_rejected_and_breaker():
    findings = oa.build_findings("en", _cluster(), [], [
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
def test_build_findings_disk_watermark_tiers_are_mutually_exclusive(disk_pct, expected_substr):
    watermarks = {"low": 85.0, "high": 90.0, "flood_stage": 95.0}
    findings = oa.build_findings("en", _cluster(), [], [_node_row(disk_used_percent=disk_pct)],
                                  watermarks=watermarks)
    disk_findings = [f for f in findings if "watermark" in f]
    assert len(disk_findings) == 1
    assert expected_substr in disk_findings[0]


def test_build_findings_slow_queries_singular_vs_plural():
    one_slow = [{"latency_ms": 1500, "indices": "logs"}]
    findings = oa.build_findings("en", _cluster(), [], [], top_queries=one_slow)
    assert "1 long-running query >= 1000ms (worst: 1500ms on logs)" in findings

    two_slow = [{"latency_ms": 1500, "indices": "logs"}, {"latency_ms": 2000, "indices": "metrics"}]
    findings = oa.build_findings("en", _cluster(), [], [], top_queries=two_slow)
    assert any("2 long-running queries >=" in f for f in findings)


def test_build_findings_slow_queries_german():
    two_slow = [{"latency_ms": 1500, "indices": "logs"}, {"latency_ms": 2000, "indices": "metrics"}]
    findings = oa.build_findings("de", _cluster(), [], [], top_queries=two_slow)
    assert any("2 lang laufende Abfragen >=" in f for f in findings)


# ---------------------------------------------------------------------------
# run_once: error handling, partial-collection degradation, exit codes
# ---------------------------------------------------------------------------

HEALTHY_CLUSTER = {
    "status": "green", "number_of_nodes": 1, "active_shards": 1,
    "relocating_shards": 0, "initializing_shards": 0, "unassigned_shards": 0,
}
EMPTY_INDEX_STATS = {"indices": {}}
EMPTY_NODE_STATS = {"nodes": {}}
EMPTY_WATERMARKS = {
    "transient": {}, "persistent": {},
    "defaults": {"cluster": {"routing": {"allocation": {"disk": {"watermark": {
        "low": "85%", "high": "90%", "flood_stage": "95%",
    }}}}}},
}


def _healthy_responses():
    return {
        "/_cluster/health": dict(HEALTHY_CLUSTER),
        "/_stats/search,indexing,store,docs,merge,query_cache,request_cache": dict(EMPTY_INDEX_STATS),
        "/_nodes/stats/thread_pool,jvm,breaker,fs": dict(EMPTY_NODE_STATS),
        "/_cluster/settings?include_defaults=true": dict(EMPTY_WATERMARKS),
        "/_insights/top_queries?type=latency&verbose=true": make_http_error(404),
    }


def test_run_once_returns_ok_when_healthy(capsys):
    client = FakeClient(_healthy_responses())
    rc = oa.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == oa.EXIT_OK
    out = capsys.readouterr().out
    assert "no issues detected" in out


def test_run_once_returns_error_when_cluster_health_unreachable(capsys):
    responses = _healthy_responses()
    responses["/_cluster/health"] = URLError("connection refused")
    client = FakeClient(responses)
    rc = oa.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == oa.EXIT_ERROR
    assert "Cannot reach OpenSearch" in capsys.readouterr().err


def test_run_once_degrades_gracefully_when_one_collector_fails(capsys):
    responses = _healthy_responses()
    responses["/_nodes/stats/thread_pool,jvm,breaker,fs"] = make_http_error(403)
    client = FakeClient(responses)
    rc = oa.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    # cluster + indices still produced a report; the node-stats failure is
    # surfaced as a finding (severity-aware exit code) instead of aborting.
    assert rc == oa.EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "could not collect nodes" in out


def test_run_once_json_output_includes_collection_errors(capsys):
    import json as _json
    responses = _healthy_responses()
    responses["/_nodes/stats/thread_pool,jvm,breaker,fs"] = make_http_error(500)
    client = FakeClient(responses)
    rc = oa.run_once(client, "en", as_json=True, query_type="latency", query_limit=10)
    assert rc == oa.EXIT_FINDINGS
    payload = _json.loads(capsys.readouterr().out)
    assert payload["nodes"] is None
    assert "nodes" in payload["collection_errors"]
    assert any("could not collect nodes" in f for f in payload["findings"])


def test_run_once_findings_present_gives_exit_findings(capsys):
    responses = _healthy_responses()
    responses["/_cluster/health"] = {**HEALTHY_CLUSTER, "status": "red"}
    client = FakeClient(responses)
    rc = oa.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == oa.EXIT_FINDINGS


def test_run_once_query_insights_absent_is_not_a_finding(capsys):
    # A 404 from the Query Insights endpoint means "plugin not installed",
    # which is expected/normal and must not itself trigger EXIT_FINDINGS.
    client = FakeClient(_healthy_responses())
    rc = oa.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == oa.EXIT_OK
    out = capsys.readouterr().out
    assert "Query Insights plugin not available" in out


# ---------------------------------------------------------------------------
# CLI argument parsing
# ---------------------------------------------------------------------------

def test_build_arg_parser_rejects_multiple_auth_methods_via_main(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--user", "u", "--api-key", "id:secret", "--password", "p"],
    )
    with pytest.raises(SystemExit) as excinfo:
        oa.main()
    assert excinfo.value.code == 2


def test_build_arg_parser_defaults_lang_from_env(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_LANG", "de")
    parser = oa.build_arg_parser("de")
    args = parser.parse_args([])
    assert args.lang == "de"
