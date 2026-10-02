#!/usr/bin/env bash
# Legt Index-Template und Search-Pipeline für Handbuch-Chunks an (siehe
# vector/pipeline.md):
#   Search-Pipeline INDEX_PREFIX-hybrid  kombiniert BM25 und k-NN per RRF
#                                        (braucht das Plugin neural-search)
#   Index-Template  INDEX_PREFIX         für INDEX_PREFIX-*: k-NN-Feld mit
#                                        EMBEDDING_DIM, Analyzer, Metadaten,
#                                        Alias INDEX_PREFIX, Pipeline als Default
#
# Das Template gilt nur für neu angelegte Indizes. Bestehende Indizes behalten
# Mapping und Settings, das Skript warnt bei abweichender Dimension.
#
# Idempotent, nach install.sh (und ggf. users.sh) ausführen, als derselbe User.
# Authentifiziert sich wie bin/status.sh (mit Security per Admin-Zertifikat).
# Konfiguration: dieselbe install.env (siehe install.env.example).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"

usage() {
    cat <<EOF
Usage: $0 [--config <datei>]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  -h, --help        Diese Hilfe

Pflicht: EMBEDDING_DIM (Dimension des Embedding-Modells). Weitere Variablen
siehe install.env.example.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unbekannte Option: $1" >&2; usage >&2; exit 2 ;;
    esac
done

if [ -f "$CONFIG_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$CONFIG_FILE"
    set +a
fi

# --- Einstellungen (Defaults) -------------------------------------------------

BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
BIN_DIR="$BASE_DIR/bin"

INDEX_PREFIX="${INDEX_PREFIX:-manuals}"
EMBEDDING_DIM="${EMBEDDING_DIM:-}"                  # Pflicht, z.B. 1024 für bge-m3/multilingual-e5-large
EMBEDDING_SPACE="${EMBEDDING_SPACE:-cosinesimil}"   # cosinesimil, innerproduct oder l2
TEXT_ANALYZER="${TEXT_ANALYZER:-german}"            # eingebauter Sprach-Analyzer für BM25
INDEX_SHARDS="${INDEX_SHARDS:-1}"
INDEX_REPLICAS="${INDEX_REPLICAS:-}"                # leer = 0 bei single-node, sonst 1
RRF_RANK_CONSTANT="${RRF_RANK_CONSTANT:-60}"

PIPELINE="$INDEX_PREFIX-hybrid"

# --- Hilfsfunktionen ----------------------------------------------------------

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

# api <METHOD> <PATH> [JSON per stdin] — setzt RESP_STATUS/RESP_BODY.
api() {
    local method="$1" path="$2" data=()
    [ "$method" = "GET" ] || data=(-H 'Content-Type: application/json' --data-binary @-)
    RESP_STATUS="$(curl -s -o "$RESP_FILE" -w '%{http_code}' -X "$method" "${OPENSEARCH_CURL_OPTS[@]}" \
        "${data[@]}" "$OPENSEARCH_URL$path")" || RESP_STATUS="000"
    RESP_BODY="$(cat "$RESP_FILE" 2>/dev/null || true)"
}

api_ok() {
    case "$RESP_STATUS" in
        200|201) ;;
        *) die "$1 fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" ;;
    esac
}

# --- Vorbedingungen -----------------------------------------------------------

[ -n "$EMBEDDING_DIM" ] || die "EMBEDDING_DIM fehlt (Dimension des Embedding-Modells, z.B. 1024) — in install.env setzen"
[[ "$EMBEDDING_DIM" =~ ^[0-9]+$ ]] || die "EMBEDDING_DIM muss eine Zahl sein (ist: $EMBEDDING_DIM)"
case "$EMBEDDING_SPACE" in
    cosinesimil|innerproduct|l2) ;;
    *) die "EMBEDDING_SPACE muss cosinesimil, innerproduct oder l2 sein (ist: $EMBEDDING_SPACE)" ;;
esac
[[ "$INDEX_PREFIX" =~ ^[a-z0-9][a-z0-9_-]*$ ]] || die "INDEX_PREFIX darf nur a-z, 0-9, - und _ enthalten"
[ -f "$BIN_DIR/env.sh" ] || die "Keine Installation unter $BASE_DIR, zuerst install.sh ausführen"

# shellcheck disable=SC1091
source "$BIN_DIR/env.sh"

if [ -z "$INDEX_REPLICAS" ]; then
    if [ -n "${SEED_HOSTS:-}" ]; then INDEX_REPLICAS=1; else INDEX_REPLICAS=0; fi
fi

RESP_FILE="$(mktemp)"
trap 'rm -f "$RESP_FILE"' EXIT

api GET "/_cat/plugins?h=component" </dev/null
api_ok "Abfrage der Plugins auf $OPENSEARCH_URL"
grep -q '^opensearch-knn' <<<"$RESP_BODY" || die "k-NN-Plugin nicht geladen"
if grep -q '^opensearch-neural-search' <<<"$RESP_BODY"; then
    HYBRID=true
else
    HYBRID=false
    warn "neural-search nicht geladen — keine Search-Pipeline, Agents müssen BM25 und k-NN selbst kombinieren (siehe README)"
fi

# --- 1. Search-Pipeline -------------------------------------------------------

# RRF statt gewichteter Min-Max-Normalisierung: Ein exakter Treffer auf einen
# Fehlercode zählt so gleich viel wie der beste semantische Treffer, ohne dass
# man Gewichte abstimmen muss. Gilt nur für hybrid-Queries, alle anderen
# Anfragen laufen unverändert durch.
if [ "$HYBRID" = "true" ]; then
    log "Lege Search-Pipeline $PIPELINE an (RRF, rank_constant $RRF_RANK_CONSTANT)"
    api PUT "/_search/pipeline/$PIPELINE" <<EOF
{
  "description": "BM25 + k-NN per Reciprocal Rank Fusion (vector/index.sh)",
  "phase_results_processors": [{
    "score-ranker-processor": {
      "combination": {"technique": "rrf", "rank_constant": $RRF_RANK_CONSTANT}
    }
  }]
}
EOF
    api_ok "Anlegen der Search-Pipeline $PIPELINE"
    DEFAULT_PIPELINE="\"index.search.default_pipeline\": \"$PIPELINE\","
else
    DEFAULT_PIPELINE=""
fi

# --- 2. Index-Template --------------------------------------------------------

# content.codes: zerlegt nur an Leerzeichen/Satzzeichen, nicht an - _ . / :,
# damit Fehlercodes, Teilenummern und Parameternamen (E-4711, knn.memory.limit)
# als ein Token exakt gefunden werden. Der Sprach-Analyzer würde sie zerlegen.
log "Lege Index-Template $INDEX_PREFIX an ($INDEX_PREFIX-*, Dimension $EMBEDDING_DIM, $EMBEDDING_SPACE)"
api PUT "/_index_template/$INDEX_PREFIX" <<EOF
{
  "index_patterns": ["$INDEX_PREFIX-*"],
  "priority": 100,
  "_meta": {"managed_by": "vector/index.sh"},
  "template": {
    "aliases": {"$INDEX_PREFIX": {}},
    "settings": {
      "index.knn": true,
      $DEFAULT_PIPELINE
      "number_of_shards": $INDEX_SHARDS,
      "number_of_replicas": $INDEX_REPLICAS,
      "analysis": {
        "tokenizer": {
          "codes": {"type": "pattern", "pattern": "[^\\\\p{L}\\\\p{N}\\\\-_./:]+"}
        },
        "filter": {
          "codes_trim": {"type": "pattern_replace", "pattern": "^[-_./:]+|[-_./:]+$", "replacement": ""},
          "codes_nonempty": {"type": "length", "min": 1}
        },
        "analyzer": {
          "codes": {"type": "custom", "tokenizer": "codes", "filter": ["codes_trim", "codes_nonempty", "lowercase"]}
        }
      }
    },
    "mappings": {
      "dynamic": "strict",
      "properties": {
        "manual":       {"type": "keyword"},
        "manual_title": {"type": "text", "analyzer": "$TEXT_ANALYZER",
                         "fields": {"keyword": {"type": "keyword", "ignore_above": 512}}},
        "version":      {"type": "keyword"},
        "language":     {"type": "keyword"},
        "section":      {"type": "text", "analyzer": "$TEXT_ANALYZER",
                         "fields": {"keyword": {"type": "keyword", "ignore_above": 1024}}},
        "page":         {"type": "integer"},
        "chunk_no":     {"type": "integer"},
        "content":      {"type": "text", "analyzer": "$TEXT_ANALYZER",
                         "fields": {"codes": {"type": "text", "analyzer": "codes"}}},
        "source_url":   {"type": "keyword"},
        "checksum":     {"type": "keyword"},
        "ingested_at":  {"type": "date"},
        "embedding": {
          "type": "knn_vector",
          "dimension": $EMBEDDING_DIM,
          "method": {
            "name": "hnsw",
            "engine": "faiss",
            "space_type": "$EMBEDDING_SPACE",
            "parameters": {"m": 16, "ef_construction": 128}
          }
        }
      }
    }
  }
}
EOF
api_ok "Anlegen des Index-Templates $INDEX_PREFIX"

# --- 3. Bestehende Indizes prüfen ---------------------------------------------

api GET "/$INDEX_PREFIX-*/_mapping/field/embedding" </dev/null
if [ "$RESP_STATUS" = "200" ]; then
    while read -r idx dim; do
        [ "$dim" = "$EMBEDDING_DIM" ] \
            || warn "Index $idx hat Dimension $dim statt $EMBEDDING_DIM — neu anlegen und neu einspielen"
    done < <(grep -oE '"[^"]+":\{"mappings":\{"embedding":\{"full_name":"embedding","mapping":\{"embedding":\{"type":"knn_vector","dimension":[0-9]+' <<<"$RESP_BODY" \
        | sed -E 's/^"([^"]+)".*"dimension":([0-9]+)$/\1 \2/')
fi

cat <<EOF

Fertig: Index-Template $INDEX_PREFIX für $INDEX_PREFIX-* auf $OPENSEARCH_URL
  Embedding:      $EMBEDDING_DIM Dimensionen, $EMBEDDING_SPACE (faiss/hnsw)
  Text-Analyzer:  $TEXT_ANALYZER (+ content.codes für Codes/Parameter)
  Alias:          $INDEX_PREFIX (alle $INDEX_PREFIX-*-Indizes)
  Hybride Suche:  $HYBRID${DEFAULT_PIPELINE:+ (Default-Pipeline $PIPELINE)}
  Shards/Replicas: $INDEX_SHARDS/$INDEX_REPLICAS

Indizes legt die Ingest-Pipeline an, z.B. $INDEX_PREFIX-de. Ablauf und
Beispiel-Queries: vector/pipeline.md
EOF
