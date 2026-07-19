#!/usr/bin/env bash
# Deletes the index created by stress.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [ -f "$SCRIPT_DIR/.env" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$SCRIPT_DIR/.env"
    set +a
fi

# Deleting an index needs admin/write credentials, not the read-only
# OPENSEARCH_USER that adduser.sh provisions for opensearch-analyze.py.
HOST="${OPENSEARCH_HOST:-http://localhost:9200}"
AUTH_USER="admin"
AUTH_PASSWORD="${OPENSEARCH_INITIAL_ADMIN_PASSWORD:-}"
INSECURE="${OPENSEARCH_INSECURE:-false}"
INDEX="${STRESS_INDEX:-stress-test-logs}"

usage() {
    cat <<EOF
Usage: $0 [options]

Deletes the stress-test index seeded by stress.sh.

Options:
  --host URL      OpenSearch base URL (default: \$OPENSEARCH_HOST or $HOST)
  --index NAME    Index to delete (default: \$STRESS_INDEX or $INDEX)
  --user NAME     Basic-auth user (default: admin — needs write access)
  --password PASS Basic-auth password (default: \$OPENSEARCH_INITIAL_ADMIN_PASSWORD)
  --insecure      Skip TLS certificate verification (default: \$OPENSEARCH_INSECURE)
  -h, --help      Show this help
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --index) INDEX="$2"; shift 2 ;;
        --user) AUTH_USER="$2"; shift 2 ;;
        --password) AUTH_PASSWORD="$2"; shift 2 ;;
        --insecure) INSECURE="true"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
    esac
done

CURL_OPTS=(-s)
[ "$INSECURE" = "true" ] && CURL_OPTS+=(-k)
[ -n "$AUTH_PASSWORD" ] && CURL_OPTS+=(-u "$AUTH_USER:$AUTH_PASSWORD")

echo "Deleting index $INDEX on $HOST"
curl "${CURL_OPTS[@]}" -X DELETE "$HOST/$INDEX" -o /dev/null -w '  -> HTTP %{http_code}\n'
