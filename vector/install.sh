#!/usr/bin/env bash
# Installiert OpenSearch (min-Distribution) + k-NN-Plugin auf RHEL/Rocky/Alma
# aus bereits heruntergeladenen Dateien. Das Skript lädt nichts herunter: Es
# sucht Tarball und Plugin-Zip im Home-Verzeichnis des aktuellen Users
# (ARTIFACT_DIR, Default ~), entpackt nach BASE_DIR (Default
# /opt/local/opensearch) und schreibt dort die Konfiguration. Keine systemd-Unit,
# kein root, keine Dateien außerhalb von BASE_DIR. Gestartet wird über
# BASE_DIR/bin/start.sh. Direkte Download-Links: vector/README.md
#
# Erwartete Dateien in ARTIFACT_DIR (auch eine Ebene tiefer, z.B. ~/Downloads):
#   opensearch-min-<V>-linux-<arch>.tar.gz   (+ optional .sha512 daneben)
#   opensearch-knn-<V>.0.zip                 (+ optional .sha512 daneben)
#
# Als der User ausführen, unter dem OpenSearch laufen soll (nicht root),
# BASE_DIR muss ihm gehören bzw. für ihn anlegbar sein.
#
# Idempotent: Erneutes Ausführen entpackt/installiert nur, was fehlt oder sich
# geändert hat, überschreibt nur Dateien mit geändertem Inhalt (mit Backup) und
# lässt BASE_DIR/data unangetastet. Ein Versionswechsel installiert parallel nach
# BASE_DIR/opensearch-<version> und schwenkt den Symlink BASE_DIR/current um.
#
# Konfiguration: Variablen in vector/install.env (siehe install.env.example),
# per --config <datei> oder als Umgebungsvariablen.
#
# Auth/TLS: Existiert BASE_DIR/config/opensearch.security.yml (legt vector/users.sh
# an), installiert das Skript zusätzlich das Security-Plugin
# (opensearch-security-<V>.0.zip aus ARTIFACT_DIR) und hängt die Datei an
# opensearch.yml an.
#
# Hybride Suche: Liegt opensearch-neural-search-<V>.0.zip in ARTIFACT_DIR
# (NEURAL_SEARCH=auto), installiert das Skript auch dieses Plugin. Snapshots
# landen in SNAPSHOT_DIR (path.repo), angelegt per BASE_DIR/bin/snapshot.sh.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
START_NODE=false
RESTART_NODE=false

usage() {
    cat <<EOF
Usage: $0 [--config <datei>] [--start] [--restart]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  --start           OpenSearch starten (bzw. bei Änderungen neu starten) und auf Health warten
  --restart         OpenSearch neu starten, falls es läuft und sich etwas geändert hat
  -h, --help        Diese Hilfe

Alle Einstellungen sind Variablen, siehe install.env.example.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
        --start) START_NODE=true; shift ;;
        --restart) RESTART_NODE=true; shift ;;
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

ARTIFACT_DIR="${ARTIFACT_DIR:-$HOME}"
OPENSEARCH_VERSION="${OPENSEARCH_VERSION:-}"  # leer = höchste in ARTIFACT_DIR gefundene Version
KNN_SHA512="${KNN_SHA512:-}"                  # optional: erwartete SHA-512 des k-NN-Zips
SECURITY_SHA512="${SECURITY_SHA512:-}"        # optional: erwartete SHA-512 des Security-Zips
NEURAL_SEARCH="${NEURAL_SEARCH:-auto}"        # auto = installieren, falls das Zip da ist; true; false
NEURAL_SHA512="${NEURAL_SHA512:-}"            # optional: erwartete SHA-512 des neural-search-Zips

BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
CONF_DIR="$BASE_DIR/config"
DATA_DIR="$BASE_DIR/data"
LOG_DIR="$BASE_DIR/logs"
TMP_DIR="$BASE_DIR/tmp"
RUN_DIR="$BASE_DIR/run"
BIN_DIR="$BASE_DIR/bin"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-$BASE_DIR/snapshots}"
SNAPSHOT_DIR="${SNAPSHOT_DIR%/}"
SNAPSHOT_KEEP="${SNAPSHOT_KEEP:-14}"                         # so viele Snapshots behält bin/snapshot.sh

CLUSTER_NAME="${CLUSTER_NAME:-opensearch-vector}"
NODE_NAME="${NODE_NAME:-$(hostname -s)}"
NETWORK_HOST="${NETWORK_HOST:-127.0.0.1}"
HTTP_PORT="${HTTP_PORT:-9200}"
TRANSPORT_PORT="${TRANSPORT_PORT:-9300}"
SEED_HOSTS="${SEED_HOSTS:-}"                                 # leer = single-node; sonst "host1,host2,host3"
INITIAL_CLUSTER_MANAGER_NODES="${INITIAL_CLUSTER_MANAGER_NODES:-}" # node.names, nur beim ersten Cluster-Start relevant
HEAP_SIZE="${HEAP_SIZE:-auto}"                               # auto = 50% RAM, max. 31g
MEMORY_LOCK="${MEMORY_LOCK:-false}"                          # true braucht ulimit -l unlimited

# --- Hilfsfunktionen ----------------------------------------------------------

CHANGED=false

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

# Sucht genau eine Datei namens $1 in ARTIFACT_DIR (max. eine Ebene tief).
find_artifact() {
    local name="$1" found
    found="$(find "$ARTIFACT_DIR" -maxdepth 2 -type f -name "$name" 2>/dev/null | sort)"
    [ -n "$found" ] || die "$name nicht in $ARTIFACT_DIR gefunden — Download-Links in vector/README.md"
    if [ "$(echo "$found" | wc -l)" -gt 1 ]; then
        warn "$name mehrfach gefunden, nehme $(echo "$found" | head -1):"
        echo "$found" >&2
    fi
    echo "$found" | head -1
}

# Prüft die SHA-512 gegen $2 oder, falls leer, gegen <datei>.sha512 daneben.
verify_artifact() {
    local file="$1" sha512="$2"
    if [ -z "$sha512" ] && [ -f "$file.sha512" ]; then
        sha512="$(awk '{print $1; exit}' "$file.sha512")"
    fi
    if [ -z "$sha512" ]; then
        warn "Keine Prüfsumme für $file, ungeprüft (SHA-512: $(sha512sum "$file" | awk '{print $1}'))"
        return
    fi
    echo "$sha512  $file" | sha512sum -c --status || die "SHA-512 stimmt nicht: $file"
    log "Prüfsumme ok: $file"
}

# Schreibt stdin nach dest, aber nur wenn sich der Inhalt unterscheidet.
# Die vorherige Version wird als dest.bak-<zeitstempel> gesichert.
write_file() {
    local dest="$1" mode="$2"
    local tmp
    tmp="$(mktemp "$TMP_DIR/write.XXXXXX")"
    cat >"$tmp"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        rm -f "$tmp"
    else
        if [ -f "$dest" ]; then
            cp -p "$dest" "$dest.bak-$(date +%Y%m%d%H%M%S)"
            log "Aktualisiere $dest (Backup angelegt)"
        else
            log "Lege $dest an"
        fi
        mv -f "$tmp" "$dest"
        CHANGED=true
    fi
    chmod "$mode" "$dest"
}

heap_size() {
    if [ "$HEAP_SIZE" != "auto" ]; then
        echo "$HEAP_SIZE"
        return
    fi
    # Hälfte des RAMs für den Heap; der Rest bleibt für Page-Cache und die
    # nativen faiss-Graphen (liegen außerhalb des Heaps). Über ~31g
    # verliert die JVM die Compressed OOPs.
    local mem_mb heap_mb
    mem_mb=$(( $(awk '/^MemTotal:/ {print $2}' /proc/meminfo) / 1024 ))
    heap_mb=$(( mem_mb / 2 ))
    [ "$heap_mb" -gt 31744 ] && heap_mb=31744
    [ "$heap_mb" -lt 512 ] && heap_mb=512
    echo "${heap_mb}m"
}

yaml_list() {
    # "a, b,c" -> ["a", "b", "c"]
    local IFS=',' item out=""
    for item in $1; do
        item="$(echo "$item" | xargs)"
        [ -n "$item" ] && out="${out:+$out, }\"$item\""
    done
    echo "[$out]"
}

# --- Vorbedingungen -----------------------------------------------------------

[ "$(id -u)" -ne 0 ] || die "Nicht als root ausführen — OpenSearch verweigert den Start als root. Als Dienst-User ausführen (siehe README)."
[ -f /etc/redhat-release ] || warn "Kein RHEL-kompatibles System erkannt, fahre trotzdem fort"
for cmd in curl tar sha512sum cmp ps find; do
    command -v "$cmd" >/dev/null || die "'$cmd' fehlt (dnf install -y curl tar coreutils diffutils procps-ng)"
done

case "$(uname -m)" in
    x86_64) ARCH=x64 ;;
    aarch64) ARCH=arm64 ;;
    *) die "Nicht unterstützte Architektur: $(uname -m)" ;;
esac

mkdir -p "$BASE_DIR" 2>/dev/null || true
[ -d "$BASE_DIR" ] && [ -w "$BASE_DIR" ] \
    || die "$BASE_DIR existiert nicht oder ist nicht schreibbar. Einmalig als root: install -d -o $(id -un) -g $(id -gn) $BASE_DIR"

# --- Dateien in ARTIFACT_DIR suchen -----------------------------------------

[ -d "$ARTIFACT_DIR" ] || die "ARTIFACT_DIR $ARTIFACT_DIR existiert nicht"

if [ -z "$OPENSEARCH_VERSION" ]; then
    OPENSEARCH_VERSION="$(find "$ARTIFACT_DIR" -maxdepth 2 -type f -name "opensearch-min-*-linux-${ARCH}.tar.gz" 2>/dev/null \
        | sed -E "s|.*/opensearch-min-(.+)-linux-${ARCH}\.tar\.gz$|\1|" | sort -V | tail -1)"
    [ -n "$OPENSEARCH_VERSION" ] \
        || die "Kein opensearch-min-<version>-linux-${ARCH}.tar.gz in $ARTIFACT_DIR gefunden — Download-Links in vector/README.md"
    log "Gefundene Version: $OPENSEARCH_VERSION"
fi
KNN_VERSION="${OPENSEARCH_VERSION}.0"

OS_FILE="$(find_artifact "opensearch-min-${OPENSEARCH_VERSION}-linux-${ARCH}.tar.gz")"
KNN_FILE="$(find_artifact "opensearch-knn-${KNN_VERSION}.zip")"

# Security-Plugin nur, wenn users.sh Auth eingeschaltet hat.
SECURITY_YML="$CONF_DIR/opensearch.security.yml"
SECURITY=false
if [ -f "$SECURITY_YML" ]; then
    SECURITY=true
    SECURITY_FILE="$(find_artifact "opensearch-security-${KNN_VERSION}.zip")"
fi

# neural-search (hybrid-Query, Score-Kombination) braucht nur k-NN, kein ml-commons.
NEURAL_NAME="opensearch-neural-search-${KNN_VERSION}.zip"
NEURAL=false
case "$NEURAL_SEARCH" in
    true) NEURAL=true; NEURAL_FILE="$(find_artifact "$NEURAL_NAME")" ;;
    auto)
        if find "$ARTIFACT_DIR" -maxdepth 2 -type f -name "$NEURAL_NAME" 2>/dev/null | grep -q .; then
            NEURAL=true
            NEURAL_FILE="$(find_artifact "$NEURAL_NAME")"
        fi
        ;;
    false) ;;
    *) die "NEURAL_SEARCH muss auto, true oder false sein (ist: $NEURAL_SEARCH)" ;;
esac

DIST_DIR="$BASE_DIR/opensearch-$OPENSEARCH_VERSION"
CURRENT="$BASE_DIR/current"

# --- 1. Verzeichnisse ---------------------------------------------------------

mkdir -p "$BIN_DIR" "$CONF_DIR/jvm.options.d" "$DATA_DIR" "$LOG_DIR" "$TMP_DIR" "$RUN_DIR"
chmod 0750 "$CONF_DIR" "$DATA_DIR" "$LOG_DIR" "$TMP_DIR"
mkdir -p "$SNAPSHOT_DIR" 2>/dev/null && [ -w "$SNAPSHOT_DIR" ] \
    || die "SNAPSHOT_DIR $SNAPSHOT_DIR nicht anlegbar oder nicht schreibbar"
chmod 0750 "$SNAPSHOT_DIR"

# --- 2. Prüfsummen ------------------------------------------------------------

verify_artifact "$OS_FILE" ""
verify_artifact "$KNN_FILE" "$KNN_SHA512"
[ "$SECURITY" = "false" ] || verify_artifact "$SECURITY_FILE" "$SECURITY_SHA512"
[ "$NEURAL" = "false" ] || verify_artifact "$NEURAL_FILE" "$NEURAL_SHA512"

# --- 3. Entpacken -------------------------------------------------------------

if [ -x "$DIST_DIR/bin/opensearch" ]; then
    log "OpenSearch $OPENSEARCH_VERSION bereits entpackt: $DIST_DIR"
else
    log "Entpacke $OS_FILE nach $DIST_DIR"
    rm -rf "$DIST_DIR"
    EXTRACT_TMP="$(mktemp -d "$BASE_DIR/.extract-XXXXXX")"
    trap 'rm -rf "$EXTRACT_TMP"' EXIT
    tar -xzf "$OS_FILE" -C "$EXTRACT_TMP"
    mv "$EXTRACT_TMP/opensearch-$OPENSEARCH_VERSION" "$DIST_DIR"
    rm -rf "$EXTRACT_TMP"
    trap - EXIT
    CHANGED=true
fi

# --- 4. Plugins ---------------------------------------------------------------

# install_plugin <name> <zip>: installiert das Plugin, falls es fehlt oder das Zip
# sich geändert hat. Marker außerhalb von plugins/, weil OpenSearch dort jeden
# Eintrag als Plugin lädt.
install_plugin() {
    local name="$1" file="$2"
    local marker="$DIST_DIR/.$name.installed" id
    id="$(sha512sum "$file" | awk '{print $1}')"
    if [ -f "$marker" ] && [ "$(cat "$marker")" = "$id" ] \
        && [ -f "$DIST_DIR/plugins/$name/plugin-descriptor.properties" ]; then
        log "Plugin $name bereits installiert"
        return
    fi
    log "Installiere Plugin $name aus $file"
    rm -rf "$DIST_DIR/plugins/$name" "$marker"
    # Gegen die Default-Konfiguration der Distribution, nicht gegen CONF_DIR.
    OPENSEARCH_JAVA_HOME="$DIST_DIR/jdk" OPENSEARCH_PATH_CONF="$DIST_DIR/config" \
        "$DIST_DIR/bin/opensearch-plugin" install --batch "file://$file"
    echo "$id" >"$marker"
    CHANGED=true
}

install_plugin opensearch-knn "$KNN_FILE"

if [ "$NEURAL" = "true" ]; then
    install_plugin opensearch-neural-search "$NEURAL_FILE"
elif [ "$NEURAL_SEARCH" = "false" ] && [ -d "$DIST_DIR/plugins/opensearch-neural-search" ]; then
    log "Entferne Plugin opensearch-neural-search (NEURAL_SEARCH=false)"
    rm -rf "$DIST_DIR/plugins/opensearch-neural-search" "$DIST_DIR/.opensearch-neural-search.installed"
    CHANGED=true
elif [ ! -d "$DIST_DIR/plugins/opensearch-neural-search" ]; then
    warn "$NEURAL_NAME nicht in $ARTIFACT_DIR — ohne neural-search keine hybride Suche (siehe README)"
fi
if [ -d "$DIST_DIR/plugins/opensearch-neural-search" ]; then
    HYBRID=true
else
    HYBRID=false
fi

if [ "$SECURITY" = "true" ]; then
    install_plugin opensearch-security "$SECURITY_FILE"
elif [ -d "$DIST_DIR/plugins/opensearch-security" ]; then
    # Ohne TLS-Konfiguration startet ein Node mit Security-Plugin nicht.
    log "Entferne Security-Plugin ($SECURITY_YML fehlt)"
    rm -rf "$DIST_DIR/plugins/opensearch-security" "$DIST_DIR/.opensearch-security.installed"
    CHANGED=true
fi

# Das Zip von Maven Central enthält nur die JARs, das von ci.opensearch.org
# zusätzlich lib/*.so für faiss (nmslib ist seit 3.0 veraltet: keine neuen Indizes).
if [ -f "$DIST_DIR/plugins/opensearch-knn/lib/libopensearchknn_faiss.so" ]; then
    KNN_ENGINES="faiss, lucene"
else
    KNN_ENGINES="nur lucene"
    warn "k-NN-Zip ohne native Libraries (Maven-Variante?) — nur engine=lucene nutzbar, siehe README"
fi

chmod -R go-w "$DIST_DIR"

# --- 5. Konfiguration ---------------------------------------------------------

# jvm.options und log4j2.properties gehören zur Distribution und werden bei
# jedem Lauf aus ihr übernommen. Eigene JVM-Optionen gehören nach
# config/jvm.options.d/*.options, eigene opensearch.yml-Einträge nach
# config/opensearch.local.yml.
sed -E \
    -e 's|^-Xm([sx])|## -Xm\1|' \
    -e "s|^-XX:HeapDumpPath=data|-XX:HeapDumpPath=$DATA_DIR|" \
    -e "s|^-XX:ErrorFile=logs/|-XX:ErrorFile=$LOG_DIR/|" \
    -e "s|logs/gc\.log|$LOG_DIR/gc.log|g" \
    "$DIST_DIR/config/jvm.options" | write_file "$CONF_DIR/jvm.options" 0640

write_file "$CONF_DIR/log4j2.properties" 0640 <"$DIST_DIR/config/log4j2.properties"

HEAP="$(heap_size)"
write_file "$CONF_DIR/jvm.options.d/heap.options" 0640 <<EOF
# Verwaltet von vector/install.sh (HEAP_SIZE=$HEAP_SIZE)
-Xms$HEAP
-Xmx$HEAP
EOF

if [ -n "$SEED_HOSTS" ]; then
    DISCOVERY="discovery.seed_hosts: $(yaml_list "$SEED_HOSTS")"
    if [ -n "$INITIAL_CLUSTER_MANAGER_NODES" ]; then
        DISCOVERY="$DISCOVERY
cluster.initial_cluster_manager_nodes: $(yaml_list "$INITIAL_CLUSTER_MANAGER_NODES")"
    fi
else
    DISCOVERY="discovery.type: single-node"
fi

{
    cat <<EOF
# Verwaltet von vector/install.sh — Änderungen hier werden beim nächsten Lauf
# überschrieben. Eigene Einstellungen in $CONF_DIR/opensearch.local.yml
# eintragen (wird unten angehängt) oder die Variablen in install.env ändern.
cluster.name: $CLUSTER_NAME
node.name: $NODE_NAME
path.data: $DATA_DIR
path.logs: $LOG_DIR
path.repo: ["$SNAPSHOT_DIR"]
network.host: $NETWORK_HOST
http.port: $HTTP_PORT
transport.port: $TRANSPORT_PORT
bootstrap.memory_lock: $MEMORY_LOCK
$DISCOVERY
EOF
    if [ "$SECURITY" = "true" ]; then
        echo
        echo "# --- aus $SECURITY_YML (vector/users.sh) ---"
        cat "$SECURITY_YML"
    fi
    if [ -f "$CONF_DIR/opensearch.local.yml" ]; then
        echo
        echo "# --- aus $CONF_DIR/opensearch.local.yml ---"
        cat "$CONF_DIR/opensearch.local.yml"
    fi
} | write_file "$CONF_DIR/opensearch.yml" 0640

if [ ! -f "$CONF_DIR/opensearch.keystore" ]; then
    log "Lege Keystore an"
    OPENSEARCH_JAVA_HOME="$DIST_DIR/jdk" OPENSEARCH_PATH_CONF="$CONF_DIR" \
        "$DIST_DIR/bin/opensearch-keystore" create
    chmod 0600 "$CONF_DIR/opensearch.keystore"
fi

# --- 6. Symlink auf die aktive Version ---------------------------------------

if [ "$(readlink "$CURRENT" 2>/dev/null)" != "$DIST_DIR" ]; then
    log "Setze $CURRENT -> $DIST_DIR"
    ln -sfn "$DIST_DIR" "$CURRENT"
    CHANGED=true
fi

# --- 7. Start-/Stop-Skripte ---------------------------------------------------

HEALTH_HOST="$NETWORK_HOST"
case "$HEALTH_HOST" in 0.0.0.0|_site_|_global_|_local_) HEALTH_HOST=127.0.0.1 ;; esac

# Mit Security authentifizieren sich die Skripte per Admin-Zertifikat (users.sh).
if [ "$SECURITY" = "true" ]; then
    SCHEME=https
    CURL_OPTS="(--cacert \"$CONF_DIR/certs/root-ca.pem\" --cert \"$CONF_DIR/certs/admin.pem\" --key \"$CONF_DIR/certs/admin-key.pem\")"
else
    SCHEME=http
    CURL_OPTS="()"
fi

# LD_LIBRARY_PATH: Der k-NN-Plugin lädt libopensearchknn_*.so per
# System.loadLibrary, java.library.path leitet sich unter Linux daraus ab
# (siehe opensearch-tar-install.sh im Voll-Bundle).
write_file "$BIN_DIR/env.sh" 0644 <<EOF
# Verwaltet von vector/install.sh — wird von start.sh/stop.sh/status.sh gelesen.
export OPENSEARCH_HOME="$CURRENT"
export OPENSEARCH_PATH_CONF="$CONF_DIR"
export OPENSEARCH_JAVA_HOME="$CURRENT/jdk"
export OPENSEARCH_TMPDIR="$TMP_DIR"
export LD_LIBRARY_PATH="$CURRENT/plugins/opensearch-knn/lib\${LD_LIBRARY_PATH:+:\$LD_LIBRARY_PATH}"
OPENSEARCH_PIDFILE="$RUN_DIR/opensearch.pid"
OPENSEARCH_URL="$SCHEME://$HEALTH_HOST:$HTTP_PORT"
OPENSEARCH_SECURITY=$SECURITY
OPENSEARCH_CURL_OPTS=$CURL_OPTS
OPENSEARCH_LOG="$LOG_DIR/$CLUSTER_NAME.log"
OPENSEARCH_STARTUP_LOG="$LOG_DIR/startup.log"
OPENSEARCH_SNAPSHOT_DIR="$SNAPSHOT_DIR"
OPENSEARCH_SNAPSHOT_KEEP=$SNAPSHOT_KEEP

opensearch_pid() {
    local pid
    [ -f "\$OPENSEARCH_PIDFILE" ] || return 1
    pid="\$(cat "\$OPENSEARCH_PIDFILE")"
    [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && echo "\$pid"
}
EOF

write_file "$BIN_DIR/start.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/install.sh — startet OpenSearch im Hintergrund und
# wartet, bis die HTTP-Schnittstelle antwortet.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if pid="$(opensearch_pid)"; then
    echo "OpenSearch läuft bereits (PID $pid)"
    exit 0
fi

# Bootstrap-Checks verlangen bei nicht-lokalem network.host >= 65535 offene
# Dateien; das Soft-Limit lässt sich bis zum Hard-Limit selbst anheben.
ulimit -n 65535 2>/dev/null || echo "WARN: ulimit -n 65535 nicht möglich (aktuell $(ulimit -n), Hard-Limit $(ulimit -Hn)) — siehe README" >&2

cd "$OPENSEARCH_HOME"
rm -f "$OPENSEARCH_PIDFILE"
# Der Daemon erbt sonst stdout/stderr und hält damit Pipes bzw. SSH-Sitzungen offen.
if ! "$OPENSEARCH_HOME/bin/opensearch" -d -p "$OPENSEARCH_PIDFILE" \
    </dev/null >"$OPENSEARCH_STARTUP_LOG" 2>&1; then
    tail -n 30 "$OPENSEARCH_STARTUP_LOG" >&2
    echo "FEHLER: Start fehlgeschlagen, siehe $OPENSEARCH_STARTUP_LOG und $OPENSEARCH_LOG" >&2
    exit 1
fi

echo "Warte auf $OPENSEARCH_URL ..."
for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "${OPENSEARCH_CURL_OPTS[@]}" "$OPENSEARCH_URL/_cluster/health" 2>/dev/null)" || true
    # Mit Security antwortet der Node vor der Initialisierung mit 503.
    if [ "$code" = "200" ] || { [ "$OPENSEARCH_SECURITY" = "true" ] && [ "${code:-000}" != "000" ]; }; then
        echo "OpenSearch läuft (PID $(opensearch_pid))"
        exit 0
    fi
    # Die PID-Datei schreibt OpenSearch erst während des Bootstraps.
    if [ -f "$OPENSEARCH_PIDFILE" ] && ! opensearch_pid >/dev/null; then
        echo "FEHLER: OpenSearch hat sich beendet, siehe $OPENSEARCH_STARTUP_LOG und $OPENSEARCH_LOG" >&2
        exit 1
    fi
    sleep 3
done
echo "FEHLER: $OPENSEARCH_URL antwortet nicht, siehe $OPENSEARCH_LOG" >&2
exit 1
EOF

write_file "$BIN_DIR/stop.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/install.sh — beendet OpenSearch per SIGTERM.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if ! pid="$(opensearch_pid)"; then
    echo "OpenSearch läuft nicht"
    rm -f "$OPENSEARCH_PIDFILE"
    exit 0
fi

echo "Stoppe OpenSearch (PID $pid) ..."
kill "$pid"
for _ in $(seq 1 120); do
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$OPENSEARCH_PIDFILE"
        echo "OpenSearch gestoppt"
        exit 0
    fi
    sleep 1
done
echo "FEHLER: OpenSearch (PID $pid) läuft nach 120s noch — ggf. kill -9 $pid" >&2
exit 1
EOF

write_file "$BIN_DIR/status.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/install.sh — zeigt Prozess- und Cluster-Status.
set -uo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if pid="$(opensearch_pid)"; then
    echo "Prozess:  läuft (PID $pid)"
else
    echo "Prozess:  läuft nicht"
    exit 3
fi
curl -fs "${OPENSEARCH_CURL_OPTS[@]}" "$OPENSEARCH_URL/_cluster/health?pretty" || { echo "HTTP:     $OPENSEARCH_URL antwortet nicht"; exit 1; }
curl -fs "${OPENSEARCH_CURL_OPTS[@]}" "$OPENSEARCH_URL/_cat/plugins?v"
EOF

write_file "$BIN_DIR/snapshot.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/install.sh — legt einen Snapshot aller Indizes im
# Repository "backup" (OPENSEARCH_SNAPSHOT_DIR) an und löscht die ältesten, bis
# nur noch OPENSEARCH_SNAPSHOT_KEEP übrig sind. Für Cron geeignet.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"
REPO=backup

req() {
    local method="$1" path="$2" data=() out code
    [ $# -lt 3 ] || data=(-H 'Content-Type: application/json' --data-binary "$3")
    out="$(curl -sS -w '\n%{http_code}' -X "$method" "${OPENSEARCH_CURL_OPTS[@]}" "${data[@]}" \
        "$OPENSEARCH_URL$path" 2>&1)" || true
    code="${out##*$'\n'}"
    out="${out%$'\n'*}"
    case "$code" in
        2??) printf '%s' "$out" ;;
        *) echo "FEHLER: $method $path (HTTP $code): $out" >&2; exit 1 ;;
    esac
}

# Idempotent: legt das Repository an bzw. bestätigt den Pfad.
req PUT "/_snapshot/$REPO" "{\"type\": \"fs\", \"settings\": {\"location\": \"$OPENSEARCH_SNAPSHOT_DIR\"}}" >/dev/null

name="snap-$(date +%Y%m%d-%H%M%S-%3N)"
echo "Lege Snapshot $REPO/$name an ..."
result="$(req PUT "/_snapshot/$REPO/$name?wait_for_completion=true")"
state="$(grep -o '"state":"[A-Z_]*"' <<<"$result" | head -1 | cut -d'"' -f4)"
if [ "$state" != "SUCCESS" ]; then
    echo "FEHLER: Snapshot $name hat Status ${state:-unbekannt}: $result" >&2
    exit 1
fi
echo "Snapshot $name ok"

mapfile -t snaps < <(req GET "/_cat/snapshots/$REPO?h=id&s=start_epoch")
excess=$(( ${#snaps[@]} - OPENSEARCH_SNAPSHOT_KEEP ))
for (( i = 0; i < excess; i++ )); do
    old="$(echo "${snaps[$i]}" | xargs)"
    [ -n "$old" ] || continue
    echo "Lösche alten Snapshot $old"
    req DELETE "/_snapshot/$REPO/$old" >/dev/null
done
EOF

# --- 8. Systemvoraussetzungen prüfen (ändert nichts außerhalb BASE_DIR) -------

case "$NETWORK_HOST" in
    127.0.0.1|localhost|::1|_local_) ;;
    *)
        # Bei nicht-lokalem network.host sind Bootstrap-Checks Pflicht.
        [ "$(sysctl -n vm.max_map_count 2>/dev/null || echo 0)" -ge 262144 ] \
            || warn "vm.max_map_count < 262144 — OpenSearch startet so nicht (siehe README, braucht root)"
        [ "$(ulimit -Hn)" = "unlimited" ] || [ "$(ulimit -Hn)" -ge 65535 ] \
            || warn "Hard-Limit offene Dateien $(ulimit -Hn) < 65535 (siehe README, braucht root)"
        ;;
esac
if [ "$MEMORY_LOCK" = "true" ] && [ "$(ulimit -Hl)" != "unlimited" ]; then
    warn "MEMORY_LOCK=true, aber ulimit -l ist $(ulimit -Hl) — Start schlägt fehl (siehe README)"
fi

# --- 9. Starten / Neustarten --------------------------------------------------

RUNNING=false
if [ -f "$RUN_DIR/opensearch.pid" ] && kill -0 "$(cat "$RUN_DIR/opensearch.pid")" 2>/dev/null; then
    RUNNING=true
fi

if [ "$RUNNING" = "true" ] && [ "$CHANGED" = "true" ]; then
    if [ "$START_NODE" = "true" ] || [ "$RESTART_NODE" = "true" ]; then
        log "Starte OpenSearch neu (Änderungen)"
        "$BIN_DIR/stop.sh"
        "$BIN_DIR/start.sh"
    else
        warn "Es gab Änderungen, OpenSearch läuft noch mit dem alten Stand: $BIN_DIR/stop.sh && $BIN_DIR/start.sh"
    fi
elif [ "$RUNNING" = "false" ] && [ "$START_NODE" = "true" ]; then
    "$BIN_DIR/start.sh"
fi

cat <<EOF

Fertig: OpenSearch $OPENSEARCH_VERSION + k-NN $KNN_VERSION ($KNN_ENGINES) unter $BASE_DIR
  Hybride Suche: $HYBRID (neural-search)
  URL:           $SCHEME://$HEALTH_HOST:$HTTP_PORT (Auth/TLS: $SECURITY)
  Binaries:      $CURRENT -> $DIST_DIR
  Konfiguration: $CONF_DIR (eigene Settings: opensearch.local.yml, jvm.options.d/)
  Daten / Logs:  $DATA_DIR / $LOG_DIR
  Snapshots:     $SNAPSHOT_DIR (behalte $SNAPSHOT_KEEP, $BIN_DIR/snapshot.sh)
  Heap:          $HEAP

Starten:   $BIN_DIR/start.sh
Stoppen:   $BIN_DIR/stop.sh
Status:    $BIN_DIR/status.sh
Snapshot:  $BIN_DIR/snapshot.sh
EOF
