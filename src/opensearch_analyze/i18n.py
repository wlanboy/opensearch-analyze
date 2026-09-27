"""Translation of report text and CLI help (en/de).

The JSON report's field names are a stable machine-readable contract and are
never translated — only the text in Translator's tables is.
"""

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
        "en": "Long-running queries (top {n} by {metric}, Query Insights)",
        "de": "Lang laufende Abfragen (Top {n} nach {metric}, Query Insights)",
    },
    "metric_latency": {"en": "latency", "de": "Latenz"},
    "metric_cpu": {"en": "CPU time", "de": "CPU-Zeit"},
    "metric_memory": {"en": "memory", "de": "Speicher"},
    "headers_long_queries": {
        "en": ["latency", "cpu", "memory", "indices", "shards", "search_type", "at", "query"],
        "de": ["latenz", "cpu", "speicher", "indizes", "shards", "suchtyp", "um", "abfrage"],
    },
    "long_queries_plugin_unavailable_msg": {
        "en": "Query Insights plugin not installed, or top-N collection for {metric} not enabled "
              "(search.insights.top_queries.{type}.enabled)",
        "de": "Query-Insights-Plugin nicht installiert oder Top-N-Erfassung für {metric} nicht aktiviert "
              "(search.insights.top_queries.{type}.enabled)",
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
        "en": "node '{node}': disk at {pct}% ({free} free) reached flood-stage watermark ({wm}) — indices on "
              "this node are likely forced read-only",
        "de": "Node '{node}': Disk bei {pct}% ({free} frei) hat Flood-Stage-Watermark ({wm}) erreicht — "
              "Indizes auf diesem Node sind vermutlich auf Read-Only gesetzt",
    },
    "finding_disk_high": {
        "en": "node '{node}': disk at {pct}% ({free} free) reached high watermark ({wm}) — shards are being "
              "relocated off this node",
        "de": "Node '{node}': Disk bei {pct}% ({free} frei) hat High-Watermark ({wm}) erreicht — Shards werden "
              "von diesem Node wegverlagert",
    },
    "finding_disk_low": {
        "en": "node '{node}': disk at {pct}% ({free} free) reached low watermark ({wm}) — no new shards will "
              "be allocated to this node",
        "de": "Node '{node}': Disk bei {pct}% ({free} frei) hat Low-Watermark ({wm}) erreicht — es werden "
              "keine neuen Shards mehr auf diesen Node verteilt",
    },
    "watermark_min_free": {"en": "min. {size} free", "de": "mind. {size} frei"},
    "finding_slow_queries": {
        "en": "{count} {word} >= {ms}ms (worst: {worst_ms}ms on {indices})",
        "de": "{count} {word} >= {ms}ms (schlimmste: {worst_ms}ms bei {indices})",
    },
    "finding_collection_failed": {
        "en": "could not collect {section}: {reason}",
        "de": "Sammlung von {section} fehlgeschlagen: {reason}",
    },
    "err_auth_missing": {
        "en": "OpenSearch rejected the request with HTTP {code} for {url} — the security plugin is active; "
              "pass --user/--password, --api-key or --bearer-token (or the matching OPENSEARCH_* env vars, "
              "e.g. via ./adduser.sh)",
        "de": "OpenSearch hat den Request mit HTTP {code} für {url} abgelehnt — das Security-Plugin ist aktiv; "
              "--user/--password, --api-key oder --bearer-token angeben (oder die passenden "
              "OPENSEARCH_*-Umgebungsvariablen, z. B. über ./adduser.sh)",
    },
    "err_auth_rejected": {
        "en": "OpenSearch rejected the credentials with HTTP 401 for {url} — check user/password, API key or "
              "token (for the local stack: re-run ./adduser.sh, e.g. after the volumes were recreated)",
        "de": "OpenSearch hat die Zugangsdaten mit HTTP 401 für {url} abgelehnt — User/Passwort, API-Key oder "
              "Token prüfen (lokaler Stack: ./adduser.sh erneut ausführen, z. B. nach neu angelegten Volumes)",
    },
    "err_forbidden": {
        "en": "missing permission (HTTP 403) for {url}: {reason}",
        "de": "fehlende Berechtigung (HTTP 403) für {url}: {reason}",
    },
    "err_invalid_response": {
        "en": "OpenSearch at {host} returned a response that is not valid JSON: {reason}",
        "de": "OpenSearch unter {host} lieferte eine Antwort, die kein gültiges JSON ist: {reason}",
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


class Translator:
    """Looks up translated strings/headers by key. Falls back to English for
    an unknown language rather than raising, so a typo'd --lang degrades
    instead of crashing the whole report."""

    strings = STRINGS
    section_labels = SECTION_LABELS

    @classmethod
    def t(cls, lang: str, key: str, **kwargs: object) -> str:
        """Look up a translated message string by key, formatting it with kwargs."""
        entry = cls.strings[key].get(lang, cls.strings[key]["en"])
        assert isinstance(entry, str), f"{key!r} is a header list, use headers() instead"
        return entry.format(**kwargs)

    @classmethod
    def headers(cls, lang: str, key: str) -> list[str]:
        """Look up a translated table-header list by key (no formatting)."""
        entry = cls.strings[key].get(lang, cls.strings[key]["en"])
        assert isinstance(entry, list), f"{key!r} is a message string, use t() instead"
        return entry

    @classmethod
    def section_label(cls, lang: str, section: str) -> str:
        return cls.section_labels[section].get(lang, cls.section_labels[section]["en"])

    @staticmethod
    def slow_query_word(lang: str, count: int) -> str:
        if lang == "de":
            return "lang laufende Abfrage" + ("n" if count != 1 else "")
        return "long-running quer" + ("y" if count == 1 else "ies")
