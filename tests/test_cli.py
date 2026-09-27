"""Tests for AnalyzerCli: error handling, partial-collection degradation,
severity-aware exit codes, and argument parsing. Collectors are exercised
against a FakeClient (no real network calls)."""

import json as _json
from urllib.error import URLError

import pytest
from fakes import FakeClient, make_http_error

from opensearch_analyze import cli
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
    assert "Query Insights plugin not installed, or top-N collection for latency not enabled" in out


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


FORBIDDEN_BODY = _json.dumps({"error": {
    "type": "security_exception",
    "reason": "no permissions for [cluster:admin/opensearch/insights/top_queries] and User [name=u]",
}, "status": 403}).encode()


def test_describe_401_without_credentials_asks_for_them():
    client = FakeClient({}, has_credentials=False)
    msg = AnalyzerCli.describe_request_error("en", client, make_http_error(401))
    assert "pass --user/--password" in msg


def test_describe_401_with_credentials_says_they_were_rejected():
    client = FakeClient({}, has_credentials=True)
    msg = AnalyzerCli.describe_request_error("en", client, make_http_error(401))
    assert "rejected the credentials" in msg
    assert "pass --user/--password" not in msg


def test_describe_403_names_the_missing_permission():
    client = FakeClient({}, has_credentials=True)
    msg = AnalyzerCli.describe_request_error("en", client, make_http_error(403, body=FORBIDDEN_BODY))
    assert msg.startswith("missing permission (HTTP 403)")
    assert "cluster:admin/opensearch/insights/top_queries" in msg


def test_describe_403_with_non_json_body_falls_back_to_raw_body():
    client = FakeClient({}, has_credentials=True)
    msg = AnalyzerCli.describe_request_error("en", client, make_http_error(403, body=b"forbidden!"))
    assert msg.endswith(": forbidden!")


def test_describe_invalid_json_response():
    client = FakeClient({})
    msg = AnalyzerCli.describe_request_error("en", client, URLError(ValueError("Expecting value")))
    assert "not valid JSON" in msg


def test_run_once_top_queries_forbidden_is_reported_not_plugin_absent(capsys):
    responses = _healthy_responses()
    responses["/_insights/top_queries?type=latency&verbose=true"] = make_http_error(403, body=FORBIDDEN_BODY)
    client = FakeClient(responses, has_credentials=True)
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="latency", query_limit=10)
    assert rc == EXIT_FINDINGS
    out = capsys.readouterr().out
    assert "could not collect long-running queries: missing permission" in out
    assert "not installed" not in out


def test_run_once_text_report_shows_metric_and_dash_for_unused_cache(capsys):
    responses = _healthy_responses()
    responses["/_stats/search,indexing,store,docs,merge,query_cache,request_cache"] = {
        "indices": {"logs": {"primaries": {}, "total": {}}},
    }
    responses["/_insights/top_queries?type=cpu&verbose=true"] = {"top_queries": []}
    client = FakeClient(responses)
    rc = AnalyzerCli.run_once(client, "en", as_json=False, query_type="cpu", query_limit=10)
    assert rc == EXIT_OK
    out = capsys.readouterr().out
    assert "top 0 by CPU time" in out
    logs_line = next(line for line in out.splitlines() if line.strip().startswith("logs"))
    assert logs_line.split()[-2:] == ["-", "-"]


def test_run_once_json_includes_query_type(capsys):
    responses = _healthy_responses()
    responses["/_insights/top_queries?type=memory&verbose=true"] = {"top_queries": []}
    AnalyzerCli.run_once(FakeClient(responses), "en", as_json=True, query_type="memory", query_limit=10)
    assert _json.loads(capsys.readouterr().out)["top_queries_type"] == "memory"


def test_load_dotenv_does_not_override_and_supports_export(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nexport OPENSEARCH_USER='from-file'\nOPENSEARCH_HOST=\"https://h:9200\"\n")
    monkeypatch.setenv("OPENSEARCH_USER", "from-shell")
    monkeypatch.delenv("OPENSEARCH_HOST", raising=False)
    cli.load_dotenv(env_file)
    assert cli.os.environ["OPENSEARCH_USER"] == "from-shell"
    assert cli.os.environ["OPENSEARCH_HOST"] == "https://h:9200"


def test_dotenv_paths_prefer_cwd_then_project_dir(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert cli.dotenv_paths() == [(tmp_path / ".env").resolve(), (cli.PROJECT_DIR / ".env").resolve()]


def test_dotenv_paths_deduplicate_when_run_from_project_dir(monkeypatch):
    monkeypatch.chdir(cli.PROJECT_DIR)
    assert cli.dotenv_paths() == [(cli.PROJECT_DIR / ".env").resolve()]


def test_main_loads_project_dotenv_from_other_cwd(tmp_path, monkeypatch):
    project = tmp_path / "project"
    project.mkdir()
    (project / ".env").write_text("OPENSEARCH_HOST=https://from-project:9200\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr(cli, "PROJECT_DIR", project)
    monkeypatch.delenv("OPENSEARCH_HOST", raising=False)
    for name in ("OPENSEARCH_USER", "OPENSEARCH_API_KEY", "OPENSEARCH_BEARER_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    seen = {}

    def fake_run_once(cls_, client, *args):
        seen["host"] = client.host
        return 0

    monkeypatch.setattr(AnalyzerCli, "run_once", classmethod(fake_run_once))
    monkeypatch.setattr("sys.argv", ["prog"])
    AnalyzerCli.main()
    assert seen["host"] == "https://from-project:9200"
