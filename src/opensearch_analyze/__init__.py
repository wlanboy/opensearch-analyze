"""OpenSearch service analyzer — CLI report of cluster/index/node health.

Performs the same analysis the opensearch-metrics Spring Boot service does
(polling /_stats and /_nodes/stats) but prints a human-readable report
instead of writing documents back into OpenSearch.

Report language is selectable with --lang/$OPENSEARCH_LANG (en/de); all
report text and CLI help are translated, the JSON output's field names
are not (they're a stable machine-readable contract).
"""

import argparse
import base64
import getpass
import json
import os
import ssl
import sys
import textwrap
import time
from datetime import datetime
from pathlib import Path
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


def _load_dotenv() -> None:
    """Load KEY=VALUE lines from a .env file in the current working
    directory into os.environ, without overriding variables the shell
    already set."""
    env_path = Path.cwd() / ".env"
    try:
        lines = env_path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv()

DEFAULT_HOST = os.environ.get("OPENSEARCH_HOST", "http://localhost:9200")
DEFAULT_USER = os.environ.get("OPENSEARCH_USER")
DEFAULT_PASSWORD = os.environ.get("OPENSEARCH_PASSWORD")
DEFAULT_API_KEY = os.environ.get("OPENSEARCH_API_KEY")
DEFAULT_BEARER_TOKEN = os.environ.get("OPENSEARCH_BEARER_TOKEN")
DEFAULT_CA_CERT = os.environ.get("OPENSEARCH_CA_CERT")
DEFAULT_INSECURE = os.environ.get("OPENSEARCH_INSECURE", "").strip().lower() in ("1", "true", "yes")
_env_lang = os.environ.get("OPENSEARCH_LANG", "en")
DEFAULT_LANG = _env_lang if _env_lang in ("en", "de") else "en"

HEAP_WARN_PERCENT = 85
DISK_WATERMARK_DEFAULT_LOW = 85.0
DISK_WATERMARK_DEFAULT_HIGH = 90.0
DISK_WATERMARK_DEFAULT_FLOOD = 95.0
CACHE_SAMPLE_MIN = 100
CACHE_HIT_RATIO_WARN = 50.0
QUERY_COLUMN_WRAP = 80
SLOW_QUERY_WARN_MS = 1000

# Exit codes: 0 = clean report, no findings; 1 = couldn't produce a report at
# all (connection/auth failure); 2 = report produced but findings are present
# (including partial collection failures), for monitoring/cron integration.
EXIT_OK = 0
EXIT_ERROR = 1
EXIT_FINDINGS = 2


# ---------------------------------------------------------------------------
# i18n
# ---------------------------------------------------------------------------

SECTION_LABELS = {
    "indices": {"en": "indices", "de": "Indizes"},
    "nodes": {"en": "nodes", "de": "Knoten"},
    "disk_watermarks": {"en": "disk watermarks", "de": "Disk-Watermarks"},
    "top_queries": {"en": "long-running queries", "de": "lang laufende Abfragen"},
}

STRINGS = {
    "prog_description": {
        "en": "Analyze an OpenSearch cluster (index stats, node health, cluster status).",
        "de": "Analysiert einen OpenSearch-Cluster (Index-Statistiken, Node-Zustand, Cluster-Status).",
    },
    "help_host": {
        "en": "OpenSearch base URL (default: {default}, env OPENSEARCH_HOST)",
        "de": "OpenSearch-Basis-URL (Standard: {default}, Umgebungsvariable OPENSEARCH_HOST)",
    },
    "help_json": {
        "en": "Emit machine-readable JSON instead of a text report",
        "de": "Gibt maschinenlesbares JSON statt eines Textberichts aus",
    },
    "help_lang": {
        "en": "Report/CLI language (default: {default}, env OPENSEARCH_LANG)",
        "de": "Sprache für Bericht/CLI (Standard: {default}, Umgebungsvariable OPENSEARCH_LANG)",
    },
    "help_watch": {
        "en": "Repeat the analysis on an interval until interrupted",
        "de": "Wiederholt die Analyse in einem festen Intervall, bis sie unterbrochen wird",
    },
    "help_interval": {
        "en": "Seconds between runs in --watch mode (default: 10)",
        "de": "Sekunden zwischen den Durchläufen im --watch-Modus (Standard: 10)",
    },
    "help_long_queries_type": {
        "en": "Rank long-running queries by this metric via the Query Insights plugin (default: latency)",
        "de": "Sortiert lang laufende Abfragen nach dieser Metrik über das Query-Insights-Plugin (Standard: latency)",
    },
    "help_long_queries_limit": {
        "en": "Number of long-running queries to show, 0 to disable this section (default: 10)",
        "de": "Anzahl der anzuzeigenden lang laufenden Abfragen, 0 deaktiviert diesen Abschnitt (Standard: 10)",
    },
    "help_user": {
        "en": "Basic-auth username for clusters with the security plugin enabled (env OPENSEARCH_USER); "
              "mutually exclusive with --api-key/--bearer-token",
        "de": "Basic-Auth-Benutzername für Cluster mit aktivem Security-Plugin (Umgebungsvariable OPENSEARCH_USER); "
              "schließt --api-key/--bearer-token aus",
    },
    "help_password": {
        "en": "Basic-auth password (env OPENSEARCH_PASSWORD); if --user is set without this, you'll be prompted",
        "de": "Basic-Auth-Passwort (Umgebungsvariable OPENSEARCH_PASSWORD); wenn --user ohne dieses Flag gesetzt "
              "ist, wird interaktiv nachgefragt",
    },
    "help_api_key": {
        "en": "API key credential, either 'id:secret' (encoded for you) or an already-encoded token; sent as "
              "'Authorization: ApiKey ...' (env OPENSEARCH_API_KEY); mutually exclusive with --user/--bearer-token",
        "de": "API-Key-Credential, entweder 'id:secret' (wird automatisch kodiert) oder ein bereits kodiertes "
              "Token; wird als 'Authorization: ApiKey ...' gesendet (Umgebungsvariable OPENSEARCH_API_KEY); "
              "schließt --user/--bearer-token aus",
    },
    "help_bearer_token": {
        "en": "Bearer/JWT token, sent as 'Authorization: Bearer ...' (env OPENSEARCH_BEARER_TOKEN); mutually "
              "exclusive with --user/--api-key",
        "de": "Bearer-/JWT-Token, wird als 'Authorization: Bearer ...' gesendet (Umgebungsvariable "
              "OPENSEARCH_BEARER_TOKEN); schließt --user/--api-key aus",
    },
    "help_ca_cert": {
        "en": "Path to a CA bundle for verifying a self-signed TLS certificate (env OPENSEARCH_CA_CERT)",
        "de": "Pfad zu einem CA-Bundle zur Prüfung eines selbstsignierten TLS-Zertifikats (Umgebungsvariable "
              "OPENSEARCH_CA_CERT)",
    },
    "help_insecure": {
        "en": "Skip TLS certificate verification (env OPENSEARCH_INSECURE); use only for local/dev self-signed "
              "setups",
        "de": "TLS-Zertifikatsprüfung überspringen (Umgebungsvariable OPENSEARCH_INSECURE); nur für lokale/Dev-"
              "Setups mit selbstsigniertem Zertifikat verwenden",
    },
    "password_prompt": {
        "en": "Password for {user}: ",
        "de": "Passwort für {user}: ",
    },
    "err_auth_conflict": {
        "en": "only one auth method may be used at a time, got: {methods}",
        "de": "es darf nur eine Auth-Methode gleichzeitig verwendet werden, angegeben: {methods}",
    },
    "report_header": {
        "en": "OpenSearch analysis — {host}  ({ts})",
        "de": "OpenSearch-Analyse — {host}  ({ts})",
    },
    "watermarks_line": {
        "en": "Disk watermarks: low={low} high={high} flood_stage={flood}",
        "de": "Disk-Watermarks: low={low} high={high} flood_stage={flood}",
    },
    "title_cluster": {"en": "Cluster", "de": "Cluster"},
    "headers_cluster": {
        "en": ["status", "nodes", "active_shards", "relocating", "initializing", "unassigned"],
        "de": ["status", "knoten", "aktive_shards", "verlagernd", "initialisierend", "nicht_zugewiesen"],
    },
    "title_indices": {"en": "Indices", "de": "Indizes"},
    "section_indices": {"en": "Indices ({n})", "de": "Indizes ({n})"},
    "headers_indices": {
        "en": ["index", "docs", "size", "queries", "avg_query", "index_ops", "failed", "qc_hit%", "rc_hit%"],
        "de": ["index", "dokumente", "größe", "abfragen", "avg_abfrage", "index_ops", "fehlgeschlagen",
               "qc_treffer%", "rc_treffer%"],
    },
    "title_nodes": {"en": "Nodes", "de": "Knoten"},
    "section_nodes": {"en": "Nodes ({n})", "de": "Knoten ({n})"},
    "headers_nodes": {
        "en": ["node", "search_q", "search_rej", "search_active", "heap%", "disk%", "breaker_trips"],
        "de": ["knoten", "suche_warteschlange", "suche_abgelehnt", "suche_aktiv", "heap%", "disk%",
               "breaker_ausloesungen"],
    },
    "title_long_queries": {"en": "Long-running queries", "de": "Lang laufende Abfragen"},
    "section_long_queries": {
        "en": "Long-running queries (top {n} by latency, Query Insights)",
        "de": "Lang laufende Abfragen (Top {n} nach Latenz, Query Insights)",
    },
    "headers_long_queries": {
        "en": ["latency", "cpu", "memory", "indices", "shards", "search_type", "at", "query"],
        "de": ["latenz", "cpu", "speicher", "indizes", "shards", "suchtyp", "um", "abfrage"],
    },
    "long_queries_plugin_unavailable_msg": {
        "en": "Query Insights plugin not available on this cluster (GET /_insights/top_queries failed)",
        "de": "Query-Insights-Plugin auf diesem Cluster nicht verfügbar (GET /_insights/top_queries "
              "fehlgeschlagen)",
    },
    "section_findings": {"en": "Findings ({n})", "de": "Befunde ({n})"},
    "no_issues": {"en": "no issues detected", "de": "keine Probleme festgestellt"},
    "table_empty": {"en": "(none)", "de": "(keine)"},
    "finding_cluster_status": {
        "en": "cluster status is {status}",
        "de": "Cluster-Status ist {status}",
    },
    "finding_unassigned_shards": {
        "en": "{n} unassigned shard(s)",
        "de": "{n} nicht zugewiesene(r) Shard(s)",
    },
    "finding_indexing_failed": {
        "en": "index '{index}': {n} failed indexing operation(s)",
        "de": "Index '{index}': {n} fehlgeschlagene Indexierungsoperation(en)",
    },
    "finding_low_query_cache": {
        "en": "index '{index}': low query cache hit ratio ({pct}%)",
        "de": "Index '{index}': niedrige Query-Cache-Trefferquote ({pct}%)",
    },
    "finding_heap": {
        "en": "node '{node}': JVM heap at {pct}%",
        "de": "Node '{node}': JVM-Heap bei {pct}%",
    },
    "finding_search_rejected": {
        "en": "node '{node}': {n} rejected search task(s)",
        "de": "Node '{node}': {n} abgelehnte(r) Such-Task(s)",
    },
    "finding_breaker_tripped": {
        "en": "node '{node}': circuit breaker tripped {n} time(s)",
        "de": "Node '{node}': Circuit Breaker {n} Mal ausgelöst",
    },
    "finding_disk_flood": {
        "en": "node '{node}': disk at {pct}% >= flood-stage watermark ({wm}%) — indices on this node are "
              "likely forced read-only",
        "de": "Node '{node}': Disk bei {pct}% >= Flood-Stage-Watermark ({wm}%) — Indizes auf diesem Node sind "
              "vermutlich auf Read-Only gesetzt",
    },
    "finding_disk_high": {
        "en": "node '{node}': disk at {pct}% >= high watermark ({wm}%) — shards are being relocated off this "
              "node",
        "de": "Node '{node}': Disk bei {pct}% >= High-Watermark ({wm}%) — Shards werden von diesem Node "
              "wegverlagert",
    },
    "finding_disk_low": {
        "en": "node '{node}': disk at {pct}% >= low watermark ({wm}%) — no new shards will be allocated to "
              "this node",
        "de": "Node '{node}': Disk bei {pct}% >= Low-Watermark ({wm}%) — es werden keine neuen Shards mehr auf "
              "diesen Node verteilt",
    },
    "finding_slow_queries": {
        "en": "{count} {word} >= {ms}ms (worst: {worst_ms}ms on {indices})",
        "de": "{count} {word} >= {ms}ms (schlimmste: {worst_ms}ms bei {indices})",
    },
    "finding_collection_failed": {
        "en": "could not collect {section}: {reason}",
        "de": "Sammlung von {section} fehlgeschlagen: {reason}",
    },
    "err_auth": {
        "en": "OpenSearch rejected the request with HTTP {code} for {url} — the security plugin is likely "
              "active; pass --user/--password, --api-key or --bearer-token (or the matching OPENSEARCH_* env "
              "vars)",
        "de": "OpenSearch hat den Request mit HTTP {code} für {url} abgelehnt — vermutlich ist das "
              "Security-Plugin aktiv; --user/--password, --api-key oder --bearer-token angeben (oder die "
              "passenden OPENSEARCH_*-Umgebungsvariablen)",
    },
    "err_http": {
        "en": "OpenSearch returned HTTP {code} for {url}: {body}",
        "de": "OpenSearch antwortete mit HTTP {code} für {url}: {body}",
    },
    "err_tls": {
        "en": "TLS certificate verification failed for {host}: {reason} — pass --ca-cert <path> for a "
              "self-signed cluster CA, or --insecure to skip verification (not recommended outside local/dev "
              "use)",
        "de": "TLS-Zertifikatsprüfung für {host} fehlgeschlagen: {reason} — bei selbstsignierter Cluster-CA "
              "--ca-cert <Pfad> angeben, oder --insecure zum Überspringen der Prüfung (nicht empfohlen "
              "außerhalb von Local/Dev-Setups)",
    },
    "err_unreachable": {
        "en": "Cannot reach OpenSearch at {host}: {reason}",
        "de": "OpenSearch unter {host} nicht erreichbar: {reason}",
    },
    "stopped_message": {"en": "Stopped.", "de": "Gestoppt."},
}


def T(lang: str, key: str, **kwargs: object) -> str:
    """Look up a translated message string by key, formatting it with kwargs.
    Falls back to English for an unknown language rather than raising, so a
    typo'd --lang degrades instead of crashing the whole report."""
    entry = STRINGS[key].get(lang, STRINGS[key]["en"])
    assert isinstance(entry, str), f"{key!r} is a header list, use T_HEADERS instead"
    return entry.format(**kwargs)


def T_HEADERS(lang: str, key: str) -> list[str]:
    """Look up a translated table-header list by key (no formatting)."""
    entry = STRINGS[key].get(lang, STRINGS[key]["en"])
    assert isinstance(entry, list), f"{key!r} is a message string, use T instead"
    return entry


def section_label(lang: str, section: str) -> str:
    return SECTION_LABELS[section].get(lang, SECTION_LABELS[section]["en"])


def _slow_query_word(lang: str, count: int) -> str:
    if lang == "de":
        return "lang laufende Abfrage" + ("n" if count != 1 else "")
    return "long-running quer" + ("y" if count == 1 else "ies")


# ---------------------------------------------------------------------------
# HTTP client
# ---------------------------------------------------------------------------

class OpenSearchGetter(Protocol):
    """What the collectors actually need from a client: a host (for error
    messages) and a GET method. Structural, so test doubles don't have to
    subclass OpenSearchClient."""
    host: str

    def get(self, path: str) -> dict: ...


class OpenSearchClient:
    """Wraps host + auth + TLS settings so collectors don't juggle them individually."""

    def __init__(self, host: str, username: str | None = None, password: str | None = None,
                 api_key: str | None = None, bearer_token: str | None = None,
                 verify_tls: bool = True, ca_cert: str | None = None, timeout: float = 10.0):
        self.host = host
        self.timeout = timeout

        self._auth_header: str | None = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self._auth_header = f"Basic {token}"
        elif api_key:
            encoded = base64.b64encode(api_key.encode()).decode() if ":" in api_key else api_key
            self._auth_header = f"ApiKey {encoded}"
        elif bearer_token:
            self._auth_header = f"Bearer {bearer_token}"

        self._ssl_context: ssl.SSLContext | None = None
        if host.startswith("https://"):
            if ca_cert:
                self._ssl_context = ssl.create_default_context(cafile=ca_cert)
            elif not verify_tls:
                self._ssl_context = ssl.create_default_context()
                self._ssl_context.check_hostname = False
                self._ssl_context.verify_mode = ssl.CERT_NONE

    def get(self, path: str) -> dict:
        req = Request(f"{self.host}{path}")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        kwargs: dict[str, object] = {"timeout": self.timeout}
        if self._ssl_context is not None:
            kwargs["context"] = self._ssl_context
        with urlopen(req, **kwargs) as resp:  # type: ignore[arg-type]
            return json.load(resp)


def format_bytes(n: float) -> str:
    n = float(n)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024.0:
            return f"{n:.1f}{unit}"
        n /= 1024.0
    return f"{n:.1f}PB"


def format_ms(n: float) -> str:
    if n >= 1000:
        return f"{n / 1000:.2f}s"
    return f"{n:.0f}ms"


def format_timestamp_ms(ms: int) -> str:
    return datetime.fromtimestamp(ms / 1000).astimezone().strftime("%H:%M:%S")


# ---------------------------------------------------------------------------
# Collectors
# ---------------------------------------------------------------------------

def collect_cluster_health(client: OpenSearchGetter) -> dict:
    data = client.get("/_cluster/health")
    return {
        "status": data.get("status"),
        "number_of_nodes": data.get("number_of_nodes"),
        "active_shards": data.get("active_shards"),
        "relocating_shards": data.get("relocating_shards"),
        "initializing_shards": data.get("initializing_shards"),
        "unassigned_shards": data.get("unassigned_shards"),
    }


def collect_index_stats(client: OpenSearchGetter) -> list:
    data = client.get("/_stats/search,indexing,store,docs,merge,query_cache,request_cache")
    indices = data.get("indices", {})
    rows = []

    for index_name, entry in sorted(indices.items()):
        if index_name.startswith("."):
            continue

        primaries = entry.get("primaries", {})
        search = primaries.get("search", {})
        indexing = primaries.get("indexing", {})
        docs = primaries.get("docs", {})
        store = primaries.get("store", {})
        merges = primaries.get("merges", {})
        query_cache = primaries.get("query_cache", {})
        request_cache = primaries.get("request_cache", {})

        query_total = search.get("query_total", 0)
        query_time_ms = search.get("query_time_in_millis", 0)
        indexing_total = indexing.get("index_total", 0)
        indexing_failed = indexing.get("index_failed", 0)

        qc_hit = query_cache.get("hit_count", 0)
        qc_miss = query_cache.get("miss_count", 0)
        rc_hit = request_cache.get("hit_count", 0)
        rc_miss = request_cache.get("miss_count", 0)

        avg_query_ms = query_time_ms / query_total if query_total else 0.0
        qc_ratio = qc_hit * 100.0 / (qc_hit + qc_miss) if (qc_hit + qc_miss) else 0.0
        rc_ratio = rc_hit * 100.0 / (rc_hit + rc_miss) if (rc_hit + rc_miss) else 0.0

        rows.append({
            "index": index_name,
            "docs_count": docs.get("count", 0),
            "store_size_bytes": store.get("size_in_bytes", 0),
            "query_total": query_total,
            "avg_query_ms": avg_query_ms,
            "fetch_total": search.get("fetch_total", 0),
            "scroll_total": search.get("scroll_total", 0),
            "indexing_total": indexing_total,
            "indexing_failed": indexing_failed,
            "merges_current": merges.get("current", 0),
            "query_cache_hit_ratio": qc_ratio,
            "query_cache_samples": qc_hit + qc_miss,
            "request_cache_hit_ratio": rc_ratio,
            "request_cache_samples": rc_hit + rc_miss,
        })

    return rows


def collect_node_stats(client: OpenSearchGetter) -> list:
    data = client.get("/_nodes/stats/thread_pool,jvm,breaker,fs")
    nodes = data.get("nodes", {})
    rows = []

    for node_id, node in nodes.items():
        name = node.get("name", node_id)
        search_pool = node.get("thread_pool", {}).get("search", {})
        heap = node.get("jvm", {}).get("mem", {})
        breakers = node.get("breakers", {})
        fs_total = node.get("fs", {}).get("total", {})

        disk_total_bytes = fs_total.get("total_in_bytes", 0)
        disk_available_bytes = fs_total.get("available_in_bytes", 0)
        disk_used_percent = (
            (disk_total_bytes - disk_available_bytes) * 100.0 / disk_total_bytes if disk_total_bytes else 0.0
        )

        rows.append({
            "node": name,
            "search_queue": search_pool.get("queue", 0),
            "search_rejected": search_pool.get("rejected", 0),
            "search_active": search_pool.get("active", 0),
            "heap_used_percent": heap.get("heap_used_percent", 0),
            "heap_used_bytes": heap.get("heap_used_in_bytes", 0),
            "heap_max_bytes": heap.get("heap_max_in_bytes", 0),
            "disk_used_percent": disk_used_percent,
            "disk_available_bytes": disk_available_bytes,
            "disk_total_bytes": disk_total_bytes,
            "breaker_parent_tripped": breakers.get("parent", {}).get("tripped", 0),
            "breaker_fielddata_tripped": breakers.get("fielddata", {}).get("tripped", 0),
            "breaker_request_tripped": breakers.get("request", {}).get("tripped", 0),
        })

    return sorted(rows, key=lambda r: r["node"])


def parse_watermark_percent(value: object) -> float | None:
    """Parse a disk watermark setting into a percent-used threshold, or None
    if it's configured as an absolute size (e.g. "50gb") rather than a
    percentage — an absolute-size watermark can't be safely compared against
    a hardcoded percent default, so it's deliberately left unresolved rather
    than substituted."""
    if value is None:
        return None
    text = str(value).strip()
    if not text.endswith("%"):
        return None
    try:
        return float(text[:-1])
    except ValueError:
        return None


def collect_disk_watermarks(client: OpenSearchGetter) -> dict:
    """Effective disk allocation watermarks (persistent/transient override defaults).

    Falls back to OpenSearch's documented defaults only when a tier genuinely
    has no value for a key at all (e.g. an older cluster whose
    include_defaults response omits it); a tier that does have a value, just
    not a percentage one, is left as None rather than papered over.
    """
    data = client.get("/_cluster/settings?include_defaults=true")

    def watermark_block(scope):
        return data.get(scope, {}).get("cluster", {}).get("routing", {}) \
            .get("allocation", {}).get("disk", {}).get("watermark", {})

    persistent = watermark_block("persistent")
    transient = watermark_block("transient")
    defaults = watermark_block("defaults")

    def pick(key):
        return transient.get(key) or persistent.get(key) or defaults.get(key)

    fallback_percent = {
        "low": DISK_WATERMARK_DEFAULT_LOW,
        "high": DISK_WATERMARK_DEFAULT_HIGH,
        "flood_stage": DISK_WATERMARK_DEFAULT_FLOOD,
    }

    result = {}
    for key in ("low", "high", "flood_stage"):
        raw = pick(key)
        result[key] = fallback_percent[key] if raw is None else parse_watermark_percent(raw)
    return result


def collect_top_queries(client: OpenSearchGetter, query_type: str = "latency", limit: int = 10) -> list | None:
    """Long-running / expensive queries via the Query Insights plugin.

    Returns None if the plugin isn't installed/enabled on the target cluster
    (the endpoint 404s or is rejected), so the report can degrade gracefully.
    Any other error (5xx, network) propagates so the caller can distinguish
    "plugin not present" from "something actually went wrong".
    """
    try:
        data = client.get(f"/_insights/top_queries?type={query_type}&verbose=true")
    except HTTPError as exc:
        if exc.code in (400, 403, 404):
            return None
        raise

    rows = []
    for q in data.get("top_queries", []):
        measurements = q.get("measurements", {})
        source = q.get("source", {})
        query_summary = json.dumps(source.get("query", source), separators=(",", ":"))

        rows.append({
            "id": q.get("id"),
            "timestamp_ms": q.get("timestamp", 0),
            "latency_ms": measurements.get("latency", {}).get("number", 0),
            "cpu_ns": measurements.get("cpu", {}).get("number", 0),
            "memory_bytes": measurements.get("memory", {}).get("number", 0),
            "indices": ",".join(q.get("indices", [])) or "-",
            "search_type": q.get("search_type"),
            "total_shards": q.get("total_shards"),
            "node_id": q.get("node_id"),
            "query": query_summary,
        })

    rows.sort(key=lambda r: r["latency_ms"], reverse=True)
    return rows[:limit]


# ---------------------------------------------------------------------------
# Findings
# ---------------------------------------------------------------------------

def build_findings(lang: str, cluster: dict, indices: list, nodes: list, top_queries: list | None = None,
                    watermarks: dict | None = None) -> list:
    findings = []
    watermarks = watermarks or {}

    if cluster.get("status") in ("yellow", "red"):
        findings.append(T(lang, "finding_cluster_status", status=cluster["status"].upper()))
    if cluster.get("unassigned_shards", 0) > 0:
        findings.append(T(lang, "finding_unassigned_shards", n=cluster["unassigned_shards"]))

    for idx in indices:
        if idx["indexing_failed"] > 0:
            findings.append(T(lang, "finding_indexing_failed", index=idx["index"], n=idx["indexing_failed"]))
        if idx["query_cache_samples"] >= CACHE_SAMPLE_MIN and idx["query_cache_hit_ratio"] < CACHE_HIT_RATIO_WARN:
            findings.append(
                T(lang, "finding_low_query_cache", index=idx["index"], pct=f"{idx['query_cache_hit_ratio']:.1f}")
            )

    flood_wm = watermarks.get("flood_stage")
    high_wm = watermarks.get("high")
    low_wm = watermarks.get("low")

    for node in nodes:
        if node["heap_used_percent"] >= HEAP_WARN_PERCENT:
            findings.append(T(lang, "finding_heap", node=node["node"], pct=node["heap_used_percent"]))
        if node["search_rejected"] > 0:
            findings.append(T(lang, "finding_search_rejected", node=node["node"], n=node["search_rejected"]))
        tripped = node["breaker_parent_tripped"] + node["breaker_fielddata_tripped"] + node["breaker_request_tripped"]
        if tripped > 0:
            findings.append(T(lang, "finding_breaker_tripped", node=node["node"], n=tripped))

        disk_pct = node["disk_used_percent"]
        if flood_wm is not None and disk_pct >= flood_wm:
            findings.append(
                T(lang, "finding_disk_flood", node=node["node"], pct=f"{disk_pct:.1f}", wm=f"{flood_wm:.0f}")
            )
        elif high_wm is not None and disk_pct >= high_wm:
            findings.append(
                T(lang, "finding_disk_high", node=node["node"], pct=f"{disk_pct:.1f}", wm=f"{high_wm:.0f}")
            )
        elif low_wm is not None and disk_pct >= low_wm:
            findings.append(
                T(lang, "finding_disk_low", node=node["node"], pct=f"{disk_pct:.1f}", wm=f"{low_wm:.0f}")
            )

    if top_queries:
        slow = [q for q in top_queries if q["latency_ms"] >= SLOW_QUERY_WARN_MS]
        if slow:
            worst = slow[0]
            findings.append(T(
                lang, "finding_slow_queries",
                count=len(slow), word=_slow_query_word(lang, len(slow)),
                ms=SLOW_QUERY_WARN_MS, worst_ms=worst["latency_ms"], indices=worst["indices"],
            ))

    return findings


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def print_table(lang: str, headers: list, rows: list, wrap_widths: dict | None = None) -> None:
    """Print an aligned table. wrap_widths maps column index -> max width;
    cells in that column wrap onto continuation lines instead of being cut off."""
    if not rows:
        print(f"  {T(lang, 'table_empty')}")
        return
    wrap_widths = wrap_widths or {}

    def cell_lines(cell, col_idx):
        text = str(cell)
        limit = wrap_widths.get(col_idx)
        if limit:
            return textwrap.wrap(text, width=limit) or [""]
        return [text]

    wrapped_rows = [[cell_lines(cell, i) for i, cell in enumerate(row)] for row in rows]

    widths = [len(h) for h in headers]
    for cols_lines in wrapped_rows:
        for i, lines in enumerate(cols_lines):
            widths[i] = max([widths[i]] + [len(line) for line in lines])

    def fmt_row(cells):
        return "  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))

    print(fmt_row(headers))
    print(fmt_row(["-" * w for w in widths]))
    for cols_lines in wrapped_rows:
        for line_idx in range(max(len(lines) for lines in cols_lines)):
            cells = [lines[line_idx] if line_idx < len(lines) else "" for lines in cols_lines]
            print(fmt_row(cells))


def print_report(lang: str, host: str, cluster: dict, indices: list | None, nodes: list | None, top_queries,
                  query_limit: int, watermarks: dict | None, findings: list,
                  collection_errors: dict | None = None) -> None:
    collection_errors = collection_errors or {}
    watermarks = watermarks or {}
    ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
    print(T(lang, "report_header", host=host, ts=ts))
    print("=" * 70)

    def fmt_wm(v):
        return f"{v:.0f}%" if v is not None else "n/a"

    print(T(lang, "watermarks_line", low=fmt_wm(watermarks.get("low")), high=fmt_wm(watermarks.get("high")),
            flood=fmt_wm(watermarks.get("flood_stage"))))

    print(f"\n{T(lang, 'title_cluster')}")
    print_table(
        lang,
        T_HEADERS(lang, "headers_cluster"),
        [[
            cluster.get("status"), cluster.get("number_of_nodes"), cluster.get("active_shards"),
            cluster.get("relocating_shards"), cluster.get("initializing_shards"), cluster.get("unassigned_shards"),
        ]],
    )

    if indices is None:
        print(f"\n{T(lang, 'title_indices')}")
        print(f"  {collection_errors.get('indices', '')}")
    else:
        print(f"\n{T(lang, 'section_indices', n=len(indices))}")
        print_table(
            lang,
            T_HEADERS(lang, "headers_indices"),
            [[
                i["index"], i["docs_count"], format_bytes(i["store_size_bytes"]), i["query_total"],
                format_ms(i["avg_query_ms"]), i["indexing_total"], i["indexing_failed"],
                f"{i['query_cache_hit_ratio']:.1f}", f"{i['request_cache_hit_ratio']:.1f}",
            ] for i in indices],
        )

    if nodes is None:
        print(f"\n{T(lang, 'title_nodes')}")
        print(f"  {collection_errors.get('nodes', '')}")
    else:
        print(f"\n{T(lang, 'section_nodes', n=len(nodes))}")
        print_table(
            lang,
            T_HEADERS(lang, "headers_nodes"),
            [[
                n["node"], n["search_queue"], n["search_rejected"], n["search_active"], n["heap_used_percent"],
                f"{n['disk_used_percent']:.1f}",
                n["breaker_parent_tripped"] + n["breaker_fielddata_tripped"] + n["breaker_request_tripped"],
            ] for n in nodes],
        )

    if query_limit > 0 and top_queries is None:
        print(f"\n{T(lang, 'title_long_queries')}")
        reason = collection_errors.get("top_queries")
        print(f"  {reason if reason else T(lang, 'long_queries_plugin_unavailable_msg')}")
    elif query_limit > 0:
        print(f"\n{T(lang, 'section_long_queries', n=len(top_queries))}")
        print_table(
            lang,
            T_HEADERS(lang, "headers_long_queries"),
            [[
                format_ms(q["latency_ms"]), format_ms(q["cpu_ns"] / 1_000_000), format_bytes(q["memory_bytes"]),
                q["indices"], q["total_shards"], q["search_type"], format_timestamp_ms(q["timestamp_ms"]),
                q["query"],
            ] for q in top_queries],
            wrap_widths={7: QUERY_COLUMN_WRAP},
        )

    print(f"\n{T(lang, 'section_findings', n=len(findings))}")
    if findings:
        for f in findings:
            print(f"  ! {f}")
    else:
        print(f"  {T(lang, 'no_issues')}")
    print()


# ---------------------------------------------------------------------------
# Run loop
# ---------------------------------------------------------------------------

def describe_request_error(lang: str, client: OpenSearchGetter, exc: HTTPError | URLError) -> str:
    if isinstance(exc, HTTPError):
        if exc.code in (401, 403):
            return T(lang, "err_auth", code=exc.code, url=exc.url)
        body = exc.read().decode(errors="replace")[:200]
        return T(lang, "err_http", code=exc.code, url=exc.url, body=body)
    if isinstance(exc.reason, ssl.SSLCertVerificationError):
        return T(lang, "err_tls", host=client.host, reason=exc.reason)
    return T(lang, "err_unreachable", host=client.host, reason=exc.reason)


def _collect_optional(fn, *args, **kwargs) -> tuple:
    """Run one non-critical collector; on failure return (None, exc) instead
    of raising, so one bad section degrades gracefully rather than discarding
    every other section that already collected successfully."""
    try:
        return fn(*args, **kwargs), None
    except (HTTPError, URLError) as exc:
        return None, exc


def run_once(client: OpenSearchGetter, lang: str, as_json: bool, query_type: str, query_limit: int) -> int:
    try:
        cluster = collect_cluster_health(client)
    except (HTTPError, URLError) as exc:
        print(describe_request_error(lang, client, exc), file=sys.stderr)
        return EXIT_ERROR

    collection_errors: dict[str, str] = {}

    indices, exc = _collect_optional(collect_index_stats, client)
    if exc is not None:
        collection_errors["indices"] = describe_request_error(lang, client, exc)

    nodes, exc = _collect_optional(collect_node_stats, client)
    if exc is not None:
        collection_errors["nodes"] = describe_request_error(lang, client, exc)

    watermarks, exc = _collect_optional(collect_disk_watermarks, client)
    if exc is not None:
        collection_errors["disk_watermarks"] = describe_request_error(lang, client, exc)
    watermarks = watermarks or {}

    top_queries = None
    if query_limit > 0:
        top_queries, exc = _collect_optional(collect_top_queries, client, query_type, query_limit)
        if exc is not None:
            collection_errors["top_queries"] = describe_request_error(lang, client, exc)

    findings = build_findings(lang, cluster, indices or [], nodes or [], top_queries, watermarks)
    for section, reason in collection_errors.items():
        findings.append(T(lang, "finding_collection_failed", section=section_label(lang, section), reason=reason))

    if as_json:
        print(json.dumps({
            "timestamp": datetime.now().astimezone().isoformat(),
            "cluster": cluster,
            "indices": indices,
            "nodes": nodes,
            "top_queries": top_queries,
            "disk_watermarks": watermarks,
            "collection_errors": collection_errors,
            "findings": findings,
        }, indent=2))
    else:
        print_report(lang, client.host, cluster, indices, nodes, top_queries, query_limit, watermarks, findings,
                      collection_errors)

    return EXIT_FINDINGS if findings else EXIT_OK


def build_arg_parser(lang: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=T(lang, "prog_description"))
    parser.add_argument("--host", default=DEFAULT_HOST, help=T(lang, "help_host", default=DEFAULT_HOST))
    parser.add_argument("--json", action="store_true", help=T(lang, "help_json"))
    parser.add_argument("--lang", choices=["en", "de"], default=lang, help=T(lang, "help_lang", default=lang))
    parser.add_argument("--watch", action="store_true", help=T(lang, "help_watch"))
    parser.add_argument("--interval", type=float, default=10.0, help=T(lang, "help_interval"))
    parser.add_argument("--long-queries-type", choices=["latency", "cpu", "memory"], default="latency",
                         help=T(lang, "help_long_queries_type"))
    parser.add_argument("--long-queries-limit", type=int, default=10, help=T(lang, "help_long_queries_limit"))
    parser.add_argument("--user", "-u", default=DEFAULT_USER, help=T(lang, "help_user"))
    parser.add_argument("--password", default=DEFAULT_PASSWORD, help=T(lang, "help_password"))
    parser.add_argument("--api-key", default=DEFAULT_API_KEY, help=T(lang, "help_api_key"))
    parser.add_argument("--bearer-token", default=DEFAULT_BEARER_TOKEN, help=T(lang, "help_bearer_token"))
    parser.add_argument("--ca-cert", default=DEFAULT_CA_CERT, help=T(lang, "help_ca_cert"))
    parser.add_argument("--insecure", "-k", action="store_true", default=DEFAULT_INSECURE,
                         help=T(lang, "help_insecure"))
    return parser


def main() -> int:
    lang_pre_parser = argparse.ArgumentParser(add_help=False)
    lang_pre_parser.add_argument("--lang", choices=["en", "de"], default=DEFAULT_LANG)
    pre_args, _ = lang_pre_parser.parse_known_args()

    parser = build_arg_parser(pre_args.lang)
    args = parser.parse_args()
    lang = args.lang

    auth_flags = [flag for flag, value in
                  (("--user", args.user), ("--api-key", args.api_key), ("--bearer-token", args.bearer_token))
                  if value]
    if len(auth_flags) > 1:
        parser.error(T(lang, "err_auth_conflict", methods=", ".join(auth_flags)))

    password = args.password
    if args.user and not password:
        password = getpass.getpass(T(lang, "password_prompt", user=args.user))

    client = OpenSearchClient(
        args.host,
        username=args.user,
        password=password,
        api_key=args.api_key,
        bearer_token=args.bearer_token,
        verify_tls=not args.insecure,
        ca_cert=args.ca_cert,
    )

    if not args.watch:
        return run_once(client, lang, args.json, args.long_queries_type, args.long_queries_limit)

    try:
        while True:
            run_once(client, lang, args.json, args.long_queries_type, args.long_queries_limit)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        print(f"\n{T(lang, 'stopped_message')}")
        return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
