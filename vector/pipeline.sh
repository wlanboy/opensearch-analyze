#!/usr/bin/env bash
# Ingest- und Such-Aufrufe für Handbuch-Chunks (siehe vector/pipeline.md):
#   testdata    schreibt einen Test-Chunk mit Dummy-Vektor als NDJSON
#   bulk        spielt eine NDJSON-Datei per _bulk ein (User agent), danach _refresh
#   delete-old  löscht alle anderen Versionen eines Handbuchs (User agent)
#   search      hybrid-Query (BM25 + k-NN) auf den Alias INDEX_PREFIX (User search)
#
# Nach index.sh ausführen. URL, CA-Zertifikat und Zugangsdaten kommen aus der
# users.env von users.sh, INDEX_PREFIX und EMBEDDING_DIM aus install.env.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
USERS_ENV=""

usage() {
    cat <<EOF
Usage: $0 [--config <datei>] [--users-env <datei>] <befehl> [optionen]

Befehle:
  testdata [--index <index>] [--out <datei>]
                      Test-Chunk (pumpe-p200, E-4711) mit Dummy-Vektor schreiben
                      (Default: Index INDEX_PREFIX-de, Ausgabe stdout)
  bulk <datei>        NDJSON per _bulk einspielen und INDEX_PREFIX-* refreshen
  delete-old <index> <manual> <version>
                      Chunks von <manual> mit anderer Version als <version> löschen
  search <frage> [--manual <id>] [--size <n>] [--k <n>] [--vector-file <datei>]
                      hybrid-Query auf den Alias INDEX_PREFIX. Der Vektor kommt aus
                      <datei> (JSON-Array oder Komma-Liste), sonst Dummy-Vektor.

Optionen:
  --config <datei>     Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  --users-env <datei>  Zugangsdaten (Default: BASE_DIR/config/users.env)
  -h, --help           Diese Hilfe
EOF
}

log() { echo "==> $*" >&2; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
        --users-env) USERS_ENV="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        -*) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
        *) break ;;
    esac
done
[ $# -gt 0 ] || { usage >&2; exit 2; }
CMD="$1"; shift

if [ -f "$CONFIG_FILE" ]; then
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
fi

# --- Einstellungen (Defaults) -------------------------------------------------

BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
USERS_ENV="${USERS_ENV:-$BASE_DIR/config/users.env}"
INDEX_PREFIX="${INDEX_PREFIX:-manuals}"
EMBEDDING_DIM="${EMBEDDING_DIM:-}"

# --- Hilfsfunktionen ----------------------------------------------------------

[[ "$EMBEDDING_DIM" =~ ^[0-9]+$ ]] || die "EMBEDDING_DIM fehlt oder ist keine Zahl — in install.env setzen"

load_users_env() {
    [ -r "$USERS_ENV" ] || die "$USERS_ENV nicht lesbar, zuerst users.sh ausführen (oder --users-env)"
    # shellcheck disable=SC1090
    source "$USERS_ENV"
    [ -n "${OPENSEARCH_URL:-}" ] || die "OPENSEARCH_URL fehlt in $USERS_ENV"
    CURL_TLS=()
    [ -n "${OPENSEARCH_CACERT:-}" ] && CURL_TLS=(--cacert "$OPENSEARCH_CACERT")
}

# as_user <agent|search> — setzt AUTH für api().
as_user() {
    load_users_env
    case "$1" in
        agent)  AUTH=(-u "${OPENSEARCH_AGENT_USER:?fehlt in $USERS_ENV}:${OPENSEARCH_AGENT_PASSWORD:?fehlt in $USERS_ENV}") ;;
        search) AUTH=(-u "${OPENSEARCH_SEARCH_USER:?fehlt in $USERS_ENV}:${OPENSEARCH_SEARCH_PASSWORD:?fehlt in $USERS_ENV}") ;;
    esac
}

# api <METHOD> <PATH> [<content-type> <datei|->] — setzt RESP_STATUS/RESP_BODY.
api() {
    local method="$1" path="$2" data=()
    [ $# -lt 4 ] || data=(-H "Content-Type: $3" --data-binary "@$4")
    RESP_STATUS="$(curl -s -o "$RESP_FILE" -w '%{http_code}' -X "$method" "${CURL_TLS[@]}" "${AUTH[@]}" \
        "${data[@]}" "$OPENSEARCH_URL$path")" || RESP_STATUS="000"
    RESP_BODY="$(cat "$RESP_FILE" 2>/dev/null || true)"
}

api_ok() {
    case "$RESP_STATUS" in
        200|201) ;;
        *) die "$1 fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" ;;
    esac
}

json_str() {
    local s="${1//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '"%s"' "$s"
}

dummy_vector() {
    LC_ALL=C awk -v n="$EMBEDDING_DIM" 'BEGIN{srand(); for(i=1;i<=n;i++) printf "%s%.4f", (i>1?",":""), rand()-0.5}'
}

RESP_FILE="$(mktemp)"
trap 'rm -f "$RESP_FILE"' EXIT

# --- Befehle ------------------------------------------------------------------

cmd_testdata() {
    local index="$INDEX_PREFIX-de" out=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --index) index="$2"; shift 2 ;;
            --out) out="$2"; shift 2 ;;
            *) die "testdata: unbekannte Option $1" ;;
        esac
    done
    local doc
    doc="$(cat <<EOF
{"index": {"_index": "$index", "_id": "pumpe-p200:3.1:118"}}
{"manual": "pumpe-p200", "manual_title": "Pumpe P200 Betriebsanleitung", "version": "3.1", "language": "de", "section": "7 Störungen > 7.2 Fehlercodes", "page": 42, "chunk_no": 118, "content": "E-4711: Druckluftanschluss undicht. Dichtung am Anschluss A3 prüfen.", "source_url": "https://docs.example.com/p200-3.1.pdf#page=42", "checksum": "sha256:test", "ingested_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)", "embedding": [$(dummy_vector)]}
EOF
)"
    if [ -n "$out" ]; then
        printf '%s\n' "$doc" >"$out"
        log "Test-Chunk für $index nach $out geschrieben ($EMBEDDING_DIM Dimensionen)"
    else
        printf '%s\n' "$doc"
    fi
}

cmd_bulk() {
    [ $# -eq 1 ] || die "bulk: genau eine NDJSON-Datei angeben"
    local file="$1"
    [ -r "$file" ] || die "$file nicht lesbar"
    [ -z "$(tail -c1 "$file")" ] || die "$file muss mit einem Zeilenumbruch enden"
    as_user agent

    log "Spiele $file ein ($(wc -l <"$file") Zeilen)"
    api POST "/_bulk" application/x-ndjson "$file"
    api_ok "_bulk"
    if grep -q '"errors":true' <<<"$RESP_BODY"; then
        local n
        n="$(grep -o '"status":[45][0-9][0-9]' <<<"$RESP_BODY" | wc -l)"
        grep -oE '"type":"[^"]+","reason":"[^"]*"' <<<"$RESP_BODY" | sort | uniq -c | head -5 >&2
        die "_bulk: $n Dokument(e) abgelehnt"
    fi

    api POST "/$INDEX_PREFIX-*/_refresh"
    api_ok "_refresh"
    log "Fertig, keine Fehler"
}

cmd_delete_old() {
    [ $# -eq 3 ] || die "delete-old: <index> <manual> <version> angeben"
    local index="$1" manual="$2" version="$3"
    as_user agent

    log "Lösche $manual außer Version $version aus $index"
    api POST "/$index/_delete_by_query?refresh=true" application/json - <<EOF
{"query": {"bool": {
  "filter": [{"term": {"manual": $(json_str "$manual")}}],
  "must_not": [{"term": {"version": $(json_str "$version")}}]
}}}
EOF
    api_ok "_delete_by_query"
    log "Gelöscht: $(grep -oE '"deleted":[0-9]+' <<<"$RESP_BODY" | cut -d: -f2)"
}

cmd_search() {
    [ $# -gt 0 ] || die "search: Frage angeben"
    local question="$1"; shift
    local manual="" size=8 k=50 vector_file=""
    while [ $# -gt 0 ]; do
        case "$1" in
            --manual) manual="$2"; shift 2 ;;
            --size) size="$2"; shift 2 ;;
            --k) k="$2"; shift 2 ;;
            --vector-file) vector_file="$2"; shift 2 ;;
            *) die "search: unbekannte Option $1" ;;
        esac
    done
    [[ "$size" =~ ^[0-9]+$ && "$k" =~ ^[0-9]+$ ]] || die "--size und --k müssen Zahlen sein"

    local vec
    if [ -n "$vector_file" ]; then
        [ -r "$vector_file" ] || die "$vector_file nicht lesbar"
        vec="$(tr -d '[] \n\r\t' <"$vector_file")"
    else
        warn "kein --vector-file, nehme Dummy-Vektor: k-NN-Treffer sind zufällig"
        vec="$(dummy_vector)"
    fi
    local dim
    dim="$(awk -F, '{print NF}' <<<"$vec")"
    [ "$dim" = "$EMBEDDING_DIM" ] || die "Vektor hat $dim Dimensionen, erwartet $EMBEDDING_DIM"

    # Filter in beide Teil-Queries, sonst liefert k-NN Treffer aus anderen Handbüchern.
    local filter="[]" knn_filter=""
    if [ -n "$manual" ]; then
        filter="[{\"term\": {\"manual\": $(json_str "$manual")}}]"
        knn_filter=", \"filter\": {\"term\": {\"manual\": $(json_str "$manual")}}"
    fi

    as_user search
    api POST "/$INDEX_PREFIX/_search" application/json - <<EOF
{
  "size": $size,
  "_source": {"excludes": ["embedding"]},
  "query": {"hybrid": {"queries": [
    {"bool": {
      "must": {"multi_match": {
        "query": $(json_str "$question"),
        "fields": ["content", "content.codes^2", "section^2", "manual_title"]
      }},
      "filter": $filter
    }},
    {"knn": {"embedding": {
      "vector": [$vec],
      "k": $k$knn_filter
    }}}
  ]}}
}
EOF
    api_ok "Suche auf $INDEX_PREFIX"
    printf '%s\n' "$RESP_BODY"
}

case "$CMD" in
    testdata)   cmd_testdata "$@" ;;
    bulk)       cmd_bulk "$@" ;;
    delete-old) cmd_delete_old "$@" ;;
    search)     cmd_search "$@" ;;
    *) echo "Unbekannter Befehl: $CMD" >&2; usage >&2; exit 2 ;;
esac
