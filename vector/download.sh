#!/usr/bin/env bash
# Lädt die Dateien, die vector/install.sh braucht, nach ARTIFACT_DIR (Default ~):
#   opensearch-min-<V>-linux-<arch>.tar.gz + .sha512   (artifacts.opensearch.org)
#   opensearch-knn-<V>.0.zip                          (ci.opensearch.org, mit faiss)
#   opensearch-neural-search-<V>.0.zip                (ci.opensearch.org, außer NEURAL_SEARCH=false)
#   opensearch-security-<V>.0.zip + .sha512           (Maven Central, mit DOWNLOAD_SECURITY=true
#                                                      oder wenn users.sh Auth eingeschaltet hat)
# Mit DOWNLOAD_DASHBOARDS=true zusätzlich für vector/dashboards.sh:
#   opensearch-dashboards-min-<V>-linux-<arch>.tar.gz + .sha512   (artifacts.opensearch.org)
#   securityDashboards-<V>.zip   (ci.opensearch.org, nur wenn auch Security geladen wird)
#
# Die k-NN- und neural-search-Zips kommen aus dem Distribution-Build auf
# ci.opensearch.org, weil das Zip auf Maven Central keine nativen Libraries
# enthält (siehe README). Die Build-ID wird aus dem Manifest des Builds gelesen.
#
# Für einen Server ohne Internetzugang auf einem anderen Rechner ausführen, mit
# z.B. DOWNLOAD_ARCH=x64 und ARTIFACT_DIR=./os-download in einer eigenen
# install.env (--config), und das Verzeichnis per scp ins Home-Verzeichnis des
# Servers kopieren.
#
# Idempotent: Vorhandene Dateien mit passender Prüfsumme werden nicht erneut
# geladen. Konfiguration: dieselbe install.env (siehe install.env.example).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
DEFAULT_VERSION=3.9.0

usage() {
    cat <<EOF
Usage: $0 [--config <datei>]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  -h, --help        Diese Hilfe

Variablen: ARTIFACT_DIR (Ziel), OPENSEARCH_VERSION (Default $DEFAULT_VERSION),
DOWNLOAD_ARCH, DOWNLOAD_SECURITY, DOWNLOAD_DASHBOARDS, BUILD_ID, DASHBOARDS_BUILD_ID, NEURAL_SEARCH, KNN_SHA512,
NEURAL_SHA512, SECURITY_SHA512, BASE_DIR. Siehe install.env.example.
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

VERSION="${OPENSEARCH_VERSION:-$DEFAULT_VERSION}"
PLUGIN_VERSION="${VERSION}.0"
DEST="${ARTIFACT_DIR:-$HOME}"
DEST="${DEST%/}"
DOWNLOAD_ARCH="${DOWNLOAD_ARCH:-}"            # leer = Architektur dieses Rechners; sonst x64 oder arm64
DOWNLOAD_SECURITY="${DOWNLOAD_SECURITY:-auto}" # auto = nur, wenn users.sh Auth eingeschaltet hat; true; false
BUILD_ID="${BUILD_ID:-}"                      # leer = aus dem Manifest auf ci.opensearch.org
DOWNLOAD_DASHBOARDS="${DOWNLOAD_DASHBOARDS:-false}" # true = auch OpenSearch Dashboards (dashboards.sh)
DASHBOARDS_BUILD_ID="${DASHBOARDS_BUILD_ID:-}"      # leer = aus dem Manifest des Dashboards-Builds
NEURAL_SEARCH="${NEURAL_SEARCH:-auto}"
BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"

CORE_URL="https://artifacts.opensearch.org/releases/core/opensearch/$VERSION"
CI_URL="https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/$VERSION"
OSD_CORE_URL="https://artifacts.opensearch.org/releases/core/opensearch-dashboards/$VERSION"
OSD_CI_URL="https://ci.opensearch.org/ci/dbc/distribution-build-opensearch-dashboards/$VERSION"
MAVEN_URL="https://repo1.maven.org/maven2/org/opensearch/plugin"

# --- Hilfsfunktionen ----------------------------------------------------------

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

# Prüft $1 gegen die SHA-512 $2; ohne $2 gegen <datei>.sha512 daneben.
# Rückgabe 0 = ok, 1 = falsch, 2 = keine Prüfsumme vorhanden.
check_sha512() {
    local file="$1" sha512="$2"
    if [ -z "$sha512" ] && [ -f "$file.sha512" ]; then
        sha512="$(awk '{print $1; exit}' "$file.sha512")"
    fi
    [ -n "$sha512" ] || return 2
    echo "$sha512  $file" | sha512sum -c --status
}

# Liest die Build-ID aus dem manifest.yml unter $1.
manifest_build_id() {
    curl -fsSL "$1/manifest.yml" 2>/dev/null \
        | awk '/^build:/ {b=1; next} b && /^[^ ]/ {exit} b && $1 == "id:" {gsub(/["\047]/, "", $2); print $2; exit}' || true
}

# Lädt $1 nach $DEST/$2 (über eine .part-Datei, damit nichts Halbes liegen bleibt).
fetch() {
    local url="$1" file="$DEST/$2"
    log "Lade $2"
    curl -fL --retry 3 --progress-bar -o "$file.part" "$url" || { rm -f "$file.part"; die "Download fehlgeschlagen: $url"; }
    mv "$file.part" "$file"
}

# Lädt $2 von $1 (optional mit $1.sha512 daneben, wenn $3=true), sofern nicht
# schon mit passender Prüfsumme vorhanden, und prüft gegen $4 bzw. die .sha512.
download() {
    local base="$1" name="$2" with_sha_file="$3" expected="$4" file="$DEST/$2" rc
    if [ "$with_sha_file" = "true" ] && [ ! -f "$file.sha512" ]; then
        fetch "$base/$name.sha512" "$name.sha512"
    fi
    if [ -f "$file" ]; then
        rc=0; check_sha512 "$file" "$expected" || rc=$?
        case "$rc" in
            0) log "Schon vorhanden, Prüfsumme ok: $name"; return ;;
            2) log "Schon vorhanden (ohne Prüfsumme): $name"; return ;;
            *) warn "Prüfsumme stimmt nicht, lade neu: $name" ;;
        esac
    fi
    fetch "$base/$name" "$name"
    rc=0; check_sha512 "$file" "$expected" || rc=$?
    case "$rc" in
        0) log "Prüfsumme ok: $name" ;;
        2) ;;
        *) die "SHA-512 stimmt nicht: $file" ;;
    esac
}

# --- Vorbedingungen -----------------------------------------------------------

for cmd in curl sha512sum awk; do
    command -v "$cmd" >/dev/null || die "'$cmd' fehlt (dnf install -y curl coreutils gawk)"
done

case "$(uname -m)" in
    x86_64) HOST_ARCH=x64 ;;
    aarch64|arm64) HOST_ARCH=arm64 ;;
    *) HOST_ARCH="" ;;
esac
ARCH="${DOWNLOAD_ARCH:-$HOST_ARCH}"
case "$ARCH" in
    x64|arm64) ;;
    "") die "Architektur $(uname -m) nicht erkannt, DOWNLOAD_ARCH=x64 oder arm64 setzen" ;;
    *) die "DOWNLOAD_ARCH muss x64 oder arm64 sein (ist: $ARCH)" ;;
esac

# Die SHA-512-Werte in install.env gelten für die Architektur des Servers. Für
# eine andere Architektur nicht gegen sie prüfen.
if [ "$ARCH" != "$HOST_ARCH" ]; then
    KNN_SHA512=""
    NEURAL_SHA512=""
    SECURITY_SHA512=""
    SECURITY_DASHBOARDS_SHA512=""
fi

case "$DOWNLOAD_SECURITY" in
    true) WITH_SECURITY=true ;;
    false) WITH_SECURITY=false ;;
    auto) WITH_SECURITY=false
          [ ! -f "$BASE_DIR/config/opensearch.security.yml" ] || WITH_SECURITY=true ;;
    *) die "DOWNLOAD_SECURITY muss auto, true oder false sein (ist: $DOWNLOAD_SECURITY)" ;;
esac
case "$DOWNLOAD_DASHBOARDS" in
    true|false) ;;
    *) die "DOWNLOAD_DASHBOARDS muss true oder false sein (ist: $DOWNLOAD_DASHBOARDS)" ;;
esac

mkdir -p "$DEST"
[ -w "$DEST" ] || die "$DEST ist nicht schreibbar"

# --- Build-ID für die CI-Zips ---------------------------------------------------

if [ -z "$BUILD_ID" ]; then
    BUILD_ID="$(manifest_build_id "$CI_URL/latest/linux/$ARCH/tar/builds/opensearch")"
    [ -n "$BUILD_ID" ] || die "Build-ID für $VERSION nicht gefunden ($CI_URL/latest/...). Gibt es die Version? Sonst BUILD_ID setzen"
fi
CI_PLUGINS="$CI_URL/$BUILD_ID/linux/$ARCH/tar/builds/opensearch/plugins"

# Das Dashboards-Plugin securityDashboards kommt aus dem eigenen Dashboards-Build.
if [ "$DOWNLOAD_DASHBOARDS" = "true" ] && [ "$WITH_SECURITY" = "true" ] && [ -z "$DASHBOARDS_BUILD_ID" ]; then
    DASHBOARDS_BUILD_ID="$(manifest_build_id "$OSD_CI_URL/latest/linux/$ARCH/tar/builds/opensearch-dashboards")"
    [ -n "$DASHBOARDS_BUILD_ID" ] || die "Dashboards-Build-ID für $VERSION nicht gefunden ($OSD_CI_URL/latest/...). Sonst DASHBOARDS_BUILD_ID setzen"
fi

log "OpenSearch $VERSION, $ARCH, CI-Build $BUILD_ID, Ziel $DEST"

# --- Downloads ----------------------------------------------------------------

download "$CORE_URL" "opensearch-min-$VERSION-linux-$ARCH.tar.gz" true ""
download "$CI_PLUGINS" "opensearch-knn-$PLUGIN_VERSION.zip" false "${KNN_SHA512:-}"

case "$NEURAL_SEARCH" in
    auto|true) download "$CI_PLUGINS" "opensearch-neural-search-$PLUGIN_VERSION.zip" false "${NEURAL_SHA512:-}" ;;
    false) log "NEURAL_SEARCH=false, überspringe neural-search" ;;
    *) die "NEURAL_SEARCH muss auto, true oder false sein (ist: $NEURAL_SEARCH)" ;;
esac

if [ "$WITH_SECURITY" = "true" ]; then
    download "$MAVEN_URL/opensearch-security/$PLUGIN_VERSION" "opensearch-security-$PLUGIN_VERSION.zip" true "${SECURITY_SHA512:-}"
fi

if [ "$DOWNLOAD_DASHBOARDS" = "true" ]; then
    download "$OSD_CORE_URL" "opensearch-dashboards-min-$VERSION-linux-$ARCH.tar.gz" true ""
    if [ "$WITH_SECURITY" = "true" ]; then
        download "$OSD_CI_URL/$DASHBOARDS_BUILD_ID/linux/$ARCH/tar/builds/opensearch-dashboards/plugins" \
            "securityDashboards-$VERSION.zip" false "${SECURITY_DASHBOARDS_SHA512:-}"
    fi
fi

# --- Zusammenfassung ----------------------------------------------------------

# ci.opensearch.org liefert keine .sha512. Die Werte hier können in die
# install.env des Servers, damit install.sh die Zips nach dem Kopieren prüft.
echo
echo "Fertig. Für install.env (gilt für $ARCH):"
echo "KNN_SHA512=$(sha512sum "$DEST/opensearch-knn-$PLUGIN_VERSION.zip" | awk '{print $1}')"
if [ -f "$DEST/opensearch-neural-search-$PLUGIN_VERSION.zip" ] && [ "$NEURAL_SEARCH" != "false" ]; then
    echo "NEURAL_SHA512=$(sha512sum "$DEST/opensearch-neural-search-$PLUGIN_VERSION.zip" | awk '{print $1}')"
fi
if [ "$DOWNLOAD_DASHBOARDS" = "true" ] && [ "$WITH_SECURITY" = "true" ]; then
    echo "SECURITY_DASHBOARDS_SHA512=$(sha512sum "$DEST/securityDashboards-$VERSION.zip" | awk '{print $1}')"
fi
