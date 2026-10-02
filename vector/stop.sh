#!/usr/bin/env bash
# Stoppt OpenSearch Dashboards (falls mit dashboards.sh installiert) und danach
# OpenSearch über deren bin/stop.sh. Nicht laufende Dienste werden übersprungen.
#
# Konfiguration: dieselbe install.env (BASE_DIR, DASHBOARDS_DIR).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"

usage() {
    cat <<EOF
Usage: $0 [--config <datei>]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  -h, --help        Diese Hilfe
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

BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
DASHBOARDS_DIR="${DASHBOARDS_DIR:-$BASE_DIR/dashboards}"
DASHBOARDS_DIR="${DASHBOARDS_DIR%/}"

die() { echo "FEHLER: $*" >&2; exit 1; }

# Dashboards zuerst, sonst meldet es bis zum eigenen Stopp Verbindungsfehler.
if [ -x "$DASHBOARDS_DIR/bin/stop.sh" ]; then
    "$DASHBOARDS_DIR/bin/stop.sh"
fi

[ -x "$BASE_DIR/bin/stop.sh" ] || die "$BASE_DIR/bin/stop.sh fehlt — erst vector/install.sh ausführen"
"$BASE_DIR/bin/stop.sh"
