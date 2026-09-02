"""Tests for AnalyzerCli: error handling, partial-collection degradation,
severity-aware exit codes, and argument parsing. Collectors are exercised
against a FakeClient (no real network calls)."""

import json as _json
from urllib.error import URLError

import pytest
from fakes import FakeClient, make_http_error

from opensearch_analyze.cli import AnalyzerCli
from opensearch_analyze.constants import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK

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
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "no issues detected" in out


def test_run_once_returns_error_when_cluster_health_unreachable(capsys):
    responses = _healthy_responses()
    responses["/_cluster/health"] = URLError("connection refused")
    client = FakeClient(responses)
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == EXIT_ERROR
    assert "Cannot reach OpenSearch" in capsys.readouterr().err


def test_run_once_degrades_gracefully_when_one_collector_fails(capsys):
    responses = _healthy_responses()
    responses["/_nodes/stats/thread_pool,jvm,breaker,fs"] = make_http_error(403)
    client = FakeClient(responses)
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    # cluster + indices still produced a report; the node-stats failure is
    # surfaced as a finding (severity-aware exit code) instead of aborting.
    assert rc == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "could not collect nodes" in out


def test_run_once_json_output_includes_collection_errors(capsys):
    responses = _healthy_responses()
    responses["/_nodes/stats/thread_pool,jvm,breaker,fs"] = make_http_error(500)
    client = FakeClient(responses)
    rc = AnalyzerCli.run_once(client, "en", as_json=True, query_type="latency", query_limit=10)
    assert rc == EXIT_FINDINGS
    payload = _json.loads(capsys.readouterr().out)
    assert payload["nodes"] is None
    assert "nodes" in payload["collection_errors"]
    assert any("could not collect nodes" in f for f in payload["findings"])


def test_run_once_findings_present_gives_exit_findings(capsys):
    responses = _healthy_responses()
    responses["/_cluster/health"] = {**HEALTHY_CLUSTER, "status": "red"}
    client = FakeClient(responses)
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == EXIT_FINDINGS


def test_run_once_query_insights_absent_is_not_a_finding(capsys):
    # A 404 from the Query Insights endpoint means "plugin not installed",
    # which is expected/normal and must not itself trigger EXIT_FINDINGS.
    client = FakeClient(_healthy_responses())
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "Query Insights plugin not available" in out


def test_main_rejects_multiple_auth_methods(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        ["prog", "--user", "u", "--api-key", "id:secret", "--password", "p"],
    )
    with pytest.raises(SystemExit) as excinfo:
        AnalyzerCli.main()
    assert excinfo.value.code == 2


def test_build_arg_parser_defaults_lang_from_env(monkeypatch):
    monkeypatch.setenv("OPENSEARCH_LANG", "de")
    parser = AnalyzerCli.build_arg_parser("de")
    args = parser.parse_args([])
    assert args.lang == "de"
