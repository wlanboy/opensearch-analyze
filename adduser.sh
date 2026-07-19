#!/usr/bin/env bash
# Legt (oder rotiert) den read-only OpenSearch-User an, den opensearch-analyze.py
# per Basic-Auth nutzt, und schreibt Host/User/Passwort nach .env.
#
# Voraussetzung: Cluster-Admin-Zugangsdaten (siehe OPENSEARCH_INITIAL_ADMIN_PASSWORD
# in .env), um die Security-REST-API ansprechen zu können. Einmalig nach
# `docker compose up -d` ausführen, oder mit --host/--admin-password gegen
# einen anderen (bereits gesicherten) Cluster.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env"

if [ -f "$ENV_FILE" ]; then
    set -a
    # shellcheck disable=SC1090
    source "$ENV_FILE"
    set +a
fi

HOST="${OPENSEARCH_HOST:-https://localhost:9200}"
ADMIN_USER="admin"
ADMIN_PASSWORD="${OPENSEARCH_INITIAL_ADMIN_PASSWORD:-}"
ANALYZER_USER="opensearch-analyzer"
ANALYZER_PASSWORD=""
ANALYZER_ROLE="opensearch-analyzer-monitor"
INSECURE="${OPENSEARCH_INSECURE:-true}"

usage() {
    cat <<EOF
Usage: $0 [options]

Legt (oder rotiert) den read-only OpenSearch-User an, den opensearch-analyze.py
per Basic-Auth nutzt, und schreibt Host/User/Passwort nach $ENV_FILE.

Legt dafür auch eine eigene, minimal berechtigte Rolle an (cluster_monitor +
indices_monitor auf allen Indizes) — das eingebaute readall_and_monitor
deckt _stats (indices:monitor/stats) nicht ab und reicht daher nicht aus.

Optionen:
  --host URL             OpenSearch-Basis-URL (Default: \$OPENSEARCH_HOST oder $HOST)
  --admin-user NAME       Admin-Benutzer für die Security-API (Default: admin)
  --admin-password PASS   Admin-Passwort (Default: \$OPENSEARCH_INITIAL_ADMIN_PASSWORD, sonst Prompt)
  --user NAME             Anzulegender Analyzer-User (Default: $ANALYZER_USER)
  --password PASS         Festes Passwort statt eines generierten (nicht empfohlen)
  --role NAME             Name der anzulegenden Rolle (Default: $ANALYZER_ROLE)
  --insecure              TLS-Zertifikatsprüfung überspringen (Default bei selbstsignierten Demo-Zertifikaten)
  --no-insecure           TLS-Zertifikatsprüfung erzwingen
  -h, --help              Diese Hilfe anzeigen
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --host) HOST="$2"; shift 2 ;;
        --admin-user) ADMIN_USER="$2"; shift 2 ;;
        --admin-password) ADMIN_PASSWORD="$2"; shift 2 ;;
        --user) ANALYZER_USER="$2"; shift 2 ;;
        --password) ANALYZER_PASSWORD="$2"; shift 2 ;;
        --role) ANALYZER_ROLE="$2"; shift 2 ;;
        --insecure) INSECURE="true"; shift ;;
        --no-insecure) INSECURE="false"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unbekannte Option: $1" >&2; usage; exit 1 ;;
    esac
done

command -v curl >/dev/null || { echo "curl wird benötigt." >&2; exit 1; }
command -v python3 >/dev/null || { echo "python3 wird benötigt." >&2; exit 1; }

if [ -z "$ADMIN_PASSWORD" ]; then
    read -r -s -p "Admin-Passwort für $ADMIN_USER@$HOST: " ADMIN_PASSWORD
    echo
fi
[ -n "$ADMIN_PASSWORD" ] || { echo "Admin-Passwort darf nicht leer sein." >&2; exit 1; }

CURL_OPTS=(-s -S)
[ "$INSECURE" = "true" ] && CURL_OPTS+=(-k)

WORKDIR="$(mktemp -d)"
trap 'rm -rf "$WORKDIR"' EXIT
RESP_FILE="$WORKDIR/resp.json"

# api METHOD PATH [JSON_BODY] — setzt RESP_STATUS und RESP_BODY
api() {
    local method="$1" path="$2" body="${3:-}"
    if [ -n "$body" ]; then
        RESP_STATUS="$(curl "${CURL_OPTS[@]}" -o "$RESP_FILE" -w '%{http_code}' -X "$method" "$HOST$path" \
            -u "$ADMIN_USER:$ADMIN_PASSWORD" -H 'Content-Type: application/json' -d "$body")" || RESP_STATUS="000"
    else
        RESP_STATUS="$(curl "${CURL_OPTS[@]}" -o "$RESP_FILE" -w '%{http_code}' -X "$method" "$HOST$path" \
            -u "$ADMIN_USER:$ADMIN_PASSWORD")" || RESP_STATUS="000"
    fi
    RESP_BODY="$(cat "$RESP_FILE" 2>/dev/null || true)"
}

gen_password() {
    python3 - <<'PY'
import random
import string

lower, upper, digits, special = string.ascii_lowercase, string.ascii_uppercase, string.digits, "!@#%^*_+-="
rng = random.SystemRandom()
chars = (
    [rng.choice(lower) for _ in range(6)]
    + [rng.choice(upper) for _ in range(6)]
    + [rng.choice(digits) for _ in range(6)]
    + [rng.choice(special) for _ in range(4)]
)
rng.shuffle(chars)
print("".join(chars))
PY
}

[ -n "$ANALYZER_PASSWORD" ] || ANALYZER_PASSWORD="$(gen_password)"

echo "Prüfe Admin-Zugang zu $HOST ..."
api GET "/_cluster/health"
if [ "$RESP_STATUS" != "200" ]; then
    echo "Cluster/Admin-Zugang fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" >&2
    exit 1
fi

echo "Lege/aktualisiere User '$ANALYZER_USER' an ..."
USER_BODY="$(python3 -c '
import json, sys
print(json.dumps({
    "password": sys.argv[1],
    "backend_roles": [],
    "description": "read-only monitoring user for opensearch-analyze.py",
}))
' "$ANALYZER_PASSWORD")"
api PUT "/_plugins/_security/api/internalusers/$ANALYZER_USER" "$USER_BODY"
if [ "$RESP_STATUS" != "200" ] && [ "$RESP_STATUS" != "201" ]; then
    echo "Anlegen des Users fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" >&2
    exit 1
fi

echo "Lege/aktualisiere Rolle '$ANALYZER_ROLE' an ..."
ROLE_BODY='{
  "cluster_permissions": ["cluster_monitor"],
  "index_permissions": [
    {"index_patterns": ["*"], "allowed_actions": ["indices_monitor"]}
  ]
}'
api PUT "/_plugins/_security/api/roles/$ANALYZER_ROLE" "$ROLE_BODY"
if [ "$RESP_STATUS" != "200" ] && [ "$RESP_STATUS" != "201" ]; then
    echo "Anlegen der Rolle fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" >&2
    exit 1
fi

echo "Mappe Rolle '$ANALYZER_ROLE' auf '$ANALYZER_USER' ..."
api GET "/_plugins/_security/api/rolesmapping/$ANALYZER_ROLE"
if [ "$RESP_STATUS" = "200" ]; then
    EXISTING_MAPPING_JSON="$RESP_BODY"
else
    EXISTING_MAPPING_JSON="{}"
fi
MAPPING_BODY="$(python3 -c '
import json, sys

role, user, existing_raw = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    existing = json.loads(existing_raw).get(role, {})
except json.JSONDecodeError:
    existing = {}

users = set(existing.get("users", []))
users.add(user)

print(json.dumps({
    "backend_roles": existing.get("backend_roles", []),
    "hosts": existing.get("hosts", []),
    "users": sorted(users),
}))
' "$ANALYZER_ROLE" "$ANALYZER_USER" "$EXISTING_MAPPING_JSON")"

api PUT "/_plugins/_security/api/rolesmapping/$ANALYZER_ROLE" "$MAPPING_BODY"
if [ "$RESP_STATUS" != "200" ] && [ "$RESP_STATUS" != "201" ]; then
    echo "Rollen-Mapping fehlgeschlagen (HTTP $RESP_STATUS): $RESP_BODY" >&2
    exit 1
fi

echo "Teste Zugang mit dem neuen User ..."
TEST_STATUS="$(curl "${CURL_OPTS[@]}" -o /dev/null -w '%{http_code}' -u "$ANALYZER_USER:$ANALYZER_PASSWORD" "$HOST/_cluster/health")" || TEST_STATUS="000"
if [ "$TEST_STATUS" != "200" ]; then
    echo "Warnung: Testzugriff mit dem neuen User schlug fehl (HTTP $TEST_STATUS)." >&2
fi

set_env_var() {
    local key="$1" value="$2"
    if [ -f "$ENV_FILE" ] && grep -q "^${key}=" "$ENV_FILE"; then
        local tmp
        tmp="$(mktemp)"
        awk -v k="$key" -v v="$value" -F= 'BEGIN{OFS="="} $1==k {print k, v; next} {print}' "$ENV_FILE" > "$tmp"
        mv "$tmp" "$ENV_FILE"
    else
        echo "${key}=${value}" >> "$ENV_FILE"
    fi
}

touch "$ENV_FILE"
chmod 600 "$ENV_FILE"
set_env_var "OPENSEARCH_HOST" "$HOST"
set_env_var "OPENSEARCH_USER" "$ANALYZER_USER"
set_env_var "OPENSEARCH_PASSWORD" "$ANALYZER_PASSWORD"
set_env_var "OPENSEARCH_INSECURE" "$INSECURE"

echo
echo "Fertig. In $ENV_FILE gespeichert:"
echo "  OPENSEARCH_HOST=$HOST"
echo "  OPENSEARCH_USER=$ANALYZER_USER"
echo "  OPENSEARCH_PASSWORD=(gespeichert, nicht angezeigt)"
echo "  OPENSEARCH_INSECURE=$INSECURE"
echo
echo "opensearch-analyze.py und opensearch-analyze-d.py lesen $ENV_FILE automatisch ein."
