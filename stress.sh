#!/usr/bin/env bash
# Generates slow/expensive OpenSearch queries so the Query Insights plugin
# (/_insights/top_queries) has data for opensearch-analyze.py --long-queries-*.
set -euo pipefail

HOST="${OPENSEARCH_HOST:-http://localhost:9200}"
INDEX="${STRESS_INDEX:-stress-test-logs}"
DOC_COUNT="${DOC_COUNT:-20000}"
BATCH_SIZE="${BATCH_SIZE:-5000}"
QUERY_ROUNDS="${QUERY_ROUNDS:-20}"
PARALLEL="${PARALLEL:-8}"
SKIP_SEED=0
CLEANUP=0

usage() {
    cat <<EOF
Usage: $0 [options]

Seeds an index and fires expensive queries (wildcard, regexp, script_score,
high-cardinality aggregations, deep pagination) so OpenSearch's Query
Insights plugin records long-running queries.

Options:
  --host URL        OpenSearch base URL (default: \$OPENSEARCH_HOST or $HOST)
  --index NAME       Index to seed/query (default: $INDEX)
  --docs N           Number of documents to seed (default: $DOC_COUNT)
  --rounds N         Query rounds per query type (default: $QUERY_ROUNDS)
  --parallel N       Concurrent queries per round (default: $PARALLEL)
  --skip-seed        Reuse an already-seeded index, skip document generation
  --cleanup          Delete the stress index and exit
  -h, --help         Show this help

After running, inspect results with:
  ./opensearch-analyze.py --long-queries-type latency --long-queries-limit 10
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --index) INDEX="$2"; shift 2 ;;
        --docs) DOC_COUNT="$2"; shift 2 ;;
        --rounds) QUERY_ROUNDS="$2"; shift 2 ;;
        --parallel) PARALLEL="$2"; shift 2 ;;
        --skip-seed) SKIP_SEED=1; shift ;;
        --cleanup) CLEANUP=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT

if [ "$CLEANUP" -eq 1 ]; then
    echo "Deleting index $INDEX on $HOST"
    curl -s -X DELETE "$HOST/$INDEX" -o /dev/null -w '  -> HTTP %{http_code}\n'
    exit 0
fi

enable_query_insights() {
    echo "Enabling Query Insights top_queries collection..."
    curl -s -X PUT "$HOST/_cluster/settings" \
        -H 'Content-Type: application/json' \
        -d '{
              "persistent": {
                "search.insights.top_queries.latency.enabled": true,
                "search.insights.top_queries.latency.window_size": "10m",
                "search.insights.top_queries.latency.top_n_size": 50,
                "search.insights.top_queries.cpu.enabled": true,
                "search.insights.top_queries.cpu.window_size": "10m",
                "search.insights.top_queries.cpu.top_n_size": 50,
                "search.insights.top_queries.memory.enabled": true,
                "search.insights.top_queries.memory.window_size": "10m",
                "search.insights.top_queries.memory.top_n_size": 50
              }
            }' -o /dev/null -w '  -> HTTP %{http_code}\n'
}

create_index() {
    echo "Creating index $INDEX..."
    curl -s -X PUT "$HOST/$INDEX" \
        -H 'Content-Type: application/json' \
        -d '{
              "settings": {"number_of_shards": 2, "number_of_replicas": 1},
              "mappings": {
                "properties": {
                  "@timestamp": {"type": "date"},
                  "message": {"type": "text"},
                  "category": {"type": "keyword"},
                  "num": {"type": "long"},
                  "tags": {"type": "keyword"}
                }
              }
            }' -o /dev/null -w '  -> HTTP %{http_code}\n'
}

seed_data() {
    echo "Generating $DOC_COUNT documents..."
    awk -v n="$DOC_COUNT" -v idx="$INDEX" -v now="$(date +%s)" 'BEGIN {
        srand();
        split("lorem ipsum dolor sit amet consectetur adipiscing elit sed do eiusmod tempor incididunt ut labore et dolore magna aliqua opensearch fluentbit docker grafana prometheus kubernetes security authentication error warning critical debug info trace timeout retry connection cluster shard replica index query aggregation cache heap memory network latency", words, " ");
        nwords = 0;
        for (w in words) nwords++;
        split("auth,network,database,search,ingest,security,billing,frontend,backend,cache", cats, ",");
        ncats = 0;
        for (c in cats) ncats++;
        for (i = 0; i < n; i++) {
            print "{\"index\":{\"_index\":\"" idx "\"}}";
            msg = "";
            wc = 20 + int(rand() * 30);
            for (j = 0; j < wc; j++) {
                msg = msg " " words[1 + int(rand() * nwords)];
            }
            cat = cats[1 + int(rand() * ncats)];
            val = int(rand() * 100000);
            t1 = int(rand() * 50);
            t2 = int(rand() * 50);
            ts_ms = (now - int(rand() * 86400 * 30)) * 1000;
            printf "{\"@timestamp\":%d,\"message\":\"%s\",\"category\":\"%s\",\"num\":%d,\"tags\":[\"tag%d\",\"tag%d\"]}\n", ts_ms, msg, cat, val, t1, t2;
        }
    }' > "$WORKDIR/bulk.ndjson"

    split -l "$((BATCH_SIZE * 2))" "$WORKDIR/bulk.ndjson" "$WORKDIR/batch_"

    for batch in "$WORKDIR"/batch_*; do
        curl -s -X POST "$HOST/$INDEX/_bulk" \
            -H 'Content-Type: application/x-ndjson' \
            --data-binary "@$batch" -o /dev/null -w '  bulk -> HTTP %{http_code}\n'
    done

    curl -s -X POST "$HOST/$INDEX/_refresh" -o /dev/null
    echo "Seeding done."
}

# Deliberately expensive query bodies: leading-wildcard/regexp scans,
# a scripted score that burns CPU per doc, high-cardinality nested aggs,
# and deep pagination with a sort.
QUERY_WILDCARD='{"query":{"wildcard":{"message":{"value":"*conn*ti*n*","case_insensitive":true}}}}'
QUERY_REGEXP='{"query":{"regexp":{"message":".*[Ee]rror.*[Ww]arning.*"}}}'
QUERY_SCRIPT_SCORE='{"query":{"function_score":{"query":{"match_all":{}},"functions":[{"script_score":{"script":{"source":"long total = 0; for (int i = 0; i < 20000; i++) { total += i % 7; } return (double) (total + doc.num.value);"}}}]}}}'
QUERY_AGGS='{"size":0,"aggs":{"by_category":{"terms":{"field":"category","size":10},"aggs":{"distinct_values":{"cardinality":{"field":"num"}},"value_stats":{"extended_stats":{"field":"num"}},"top_tags":{"terms":{"field":"tags","size":20}}}}}}'
QUERY_DEEP_PAGE='{"from":9000,"size":200,"query":{"match_all":{}},"sort":[{"num":"desc"}]}'
QUERY_FUZZY='{"query":{"multi_match":{"query":"netwrok conection eror latancy","fields":["message"],"fuzziness":"AUTO"}}}'

run_query() {
    local body="$1"
    curl -s -X POST "$HOST/$INDEX/_search" \
        -H 'Content-Type: application/json' \
        -d "$body" -o /dev/null -w '  query -> HTTP %{http_code}, took %{time_total}s\n'
}

fire_rounds() {
    local bodies=("$QUERY_WILDCARD" "$QUERY_REGEXP" "$QUERY_SCRIPT_SCORE" "$QUERY_AGGS" "$QUERY_DEEP_PAGE" "$QUERY_FUZZY")
    echo "Firing $QUERY_ROUNDS rounds x ${#bodies[@]} query types, $PARALLEL concurrent..."
    for ((round = 1; round <= QUERY_ROUNDS; round++)); do
        echo "Round $round/$QUERY_ROUNDS"
        local running=0
        for body in "${bodies[@]}"; do
            run_query "$body" &
            running=$((running + 1))
            if [ "$running" -ge "$PARALLEL" ]; then
                wait
                running=0
            fi
        done
        wait
    done
}

enable_query_insights

if [ "$SKIP_SEED" -eq 0 ]; then
    create_index
    seed_data
fi

fire_rounds

echo
echo "Done. Inspect long-running queries with:"
echo "  ./opensearch-analyze.py --host $HOST --long-queries-type latency --long-queries-limit 10"
echo "  ./opensearch-analyze.py --host $HOST --long-queries-type cpu --long-queries-limit 10"
