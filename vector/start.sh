#!/usr/bin/env bash
# Startet OpenSearch und danach OpenSearch Dashboards (falls mit dashboards.sh
# installiert) über deren bin/start.sh. Beide warten, bis die HTTP-Schnittstelle
# antwortet. Bereits laufende Dienste bleiben unverändert.
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

[ -x "$BASE_DIR/bin/start.sh" ] || die "$BASE_DIR/bin/start.sh fehlt — erst vector/install.sh ausführen"
"$BASE_DIR/bin/start.sh"

if [ -x "$DASHBOARDS_DIR/bin/start.sh" ]; then
    "$DASHBOARDS_DIR/bin/start.sh"
else
    echo "OpenSearch Dashboards nicht installiert ($DASHBOARDS_DIR), überspringe"
fi
