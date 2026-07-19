#!/usr/bin/env bash
# Deletes the index created by stress.sh.
set -euo pipefail

HOST="${OPENSEARCH_HOST:-http://localhost:9200}"
INDEX="${STRESS_INDEX:-stress-test-logs}"

usage() {
    cat <<EOF
Usage: $0 [options]

Deletes the stress-test index seeded by stress.sh.

Options:
  --host URL    OpenSearch base URL (default: \$OPENSEARCH_HOST or $HOST)
  --index NAME  Index to delete (default: \$STRESS_INDEX or $INDEX)
  -h, --help    Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --index) INDEX="$2"; shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

echo "Deleting index $INDEX on $HOST"
curl -s -X DELETE "$HOST/$INDEX" -o /dev/null -w '  -> HTTP %{http_code}\n'
