#!/usr/bin/env bash
# Lädt die Dateien, die vector/install.sh braucht, nach ARTIFACT_DIR (Default ~):
#   opensearch-min-<V>-linux-<arch>.tar.gz + .sha512   (artifacts.opensearch.org)
#   opensearch-knn-<V>.0.zip                          (ci.opensearch.org, mit faiss)
#   opensearch-neural-search-<V>.0.zip                (ci.opensearch.org, außer NEURAL_SEARCH=false)
#   opensearch-security-<V>.0.zip + .sha512           (Maven Central, nur mit --security
#                                                      oder wenn users.sh Auth eingeschaltet hat)
#
# Die k-NN- und neural-search-Zips kommen aus dem Distribution-Build auf
# ci.opensearch.org, weil das Zip auf Maven Central keine nativen Libraries
# enthält (siehe README). Die Build-ID wird aus dem Manifest des Builds gelesen.
#
# Für einen Server ohne Internetzugang auf einem anderen Rechner ausführen, z.B.
#   vector/download.sh --arch x64 --dest ./os-download
# und das Verzeichnis per scp ins Home-Verzeichnis des Servers kopieren.
#
# Idempotent: Vorhandene Dateien mit passender Prüfsumme werden nicht erneut
# geladen. Konfiguration: dieselbe install.env wie install.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
DEFAULT_VERSION=3.9.0
ARG_VERSION=""
ARG_ARCH=""
ARG_DEST=""
WITH_SECURITY=false

usage() {
    cat <<EOF
Usage: $0 [--config <datei>] [--version <V>] [--arch x64|arm64] [--dest <verz>] [--security]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  --version <V>     OpenSearch-Version (Default: OPENSEARCH_VERSION, sonst $DEFAULT_VERSION)
  --arch <a>        x64 oder arm64 (Default: Architektur dieses Rechners)
  --dest <verz>     Zielverzeichnis (Default: ARTIFACT_DIR, sonst ~)
  --security        Auch das Security-Plugin laden (für users.sh)
  -h, --help        Diese Hilfe

Variablen aus install.env: ARTIFACT_DIR, OPENSEARCH_VERSION, NEURAL_SEARCH,
KNN_SHA512, NEURAL_SHA512, SECURITY_SHA512, BASE_DIR. Zusätzlich BUILD_ID
(Default: aus dem Manifest auf ci.opensearch.org).
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
        --version) ARG_VERSION="$2"; shift 2 ;;
        --arch) ARG_ARCH="$2"; shift 2 ;;
        --dest) ARG_DEST="$2"; shift 2 ;;
        --security) WITH_SECURITY=true; shift ;;
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

VERSION="${ARG_VERSION:-${OPENSEARCH_VERSION:-$DEFAULT_VERSION}}"
PLUGIN_VERSION="${VERSION}.0"
DEST="${ARG_DEST:-${ARTIFACT_DIR:-$HOME}}"
DEST="${DEST%/}"
NEURAL_SEARCH="${NEURAL_SEARCH:-auto}"
BUILD_ID="${BUILD_ID:-}"
BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"

CORE_URL="https://artifacts.opensearch.org/releases/core/opensearch/$VERSION"
CI_URL="https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/$VERSION"
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
ARCH="${ARG_ARCH:-$HOST_ARCH}"
case "$ARCH" in
    x64|arm64) ;;
    "") die "Architektur $(uname -m) nicht erkannt, bitte --arch x64 oder --arch arm64 angeben" ;;
    *) die "--arch muss x64 oder arm64 sein (ist: $ARCH)" ;;
esac

# Die SHA-512-Werte in install.env gelten für die Architektur des Servers. Für
# eine andere Architektur nicht gegen sie prüfen.
if [ "$ARCH" != "$HOST_ARCH" ]; then
    KNN_SHA512=""
    NEURAL_SHA512=""
    SECURITY_SHA512=""
fi

# Security-Plugin auch dann, wenn users.sh Auth auf diesem Rechner eingeschaltet hat.
[ ! -f "$BASE_DIR/config/opensearch.security.yml" ] || WITH_SECURITY=true

mkdir -p "$DEST"
[ -w "$DEST" ] || die "$DEST ist nicht schreibbar"

# --- Build-ID für die CI-Zips ---------------------------------------------------

if [ -z "$BUILD_ID" ]; then
    BUILD_ID="$(curl -fsSL "$CI_URL/latest/linux/$ARCH/tar/builds/opensearch/manifest.yml" 2>/dev/null \
        | awk '/^build:/ {b=1; next} b && /^[^ ]/ {exit} b && $1 == "id:" {gsub(/["\047]/, "", $2); print $2; exit}' || true)"
    [ -n "$BUILD_ID" ] || die "Build-ID für $VERSION nicht gefunden ($CI_URL/latest/...). Gibt es die Version? Sonst BUILD_ID setzen"
fi
CI_PLUGINS="$CI_URL/$BUILD_ID/linux/$ARCH/tar/builds/opensearch/plugins"

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

# --- Zusammenfassung ----------------------------------------------------------

# ci.opensearch.org liefert keine .sha512. Die Werte hier können in die
# install.env des Servers, damit install.sh die Zips nach dem Kopieren prüft.
echo
echo "Fertig. Für install.env (gilt für $ARCH):"
echo "KNN_SHA512=$(sha512sum "$DEST/opensearch-knn-$PLUGIN_VERSION.zip" | awk '{print $1}')"
if [ -f "$DEST/opensearch-neural-search-$PLUGIN_VERSION.zip" ] && [ "$NEURAL_SEARCH" != "false" ]; then
    echo "NEURAL_SHA512=$(sha512sum "$DEST/opensearch-neural-search-$PLUGIN_VERSION.zip" | awk '{print $1}')"
fi
