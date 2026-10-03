#!/usr/bin/env bash
# Installiert OpenSearch Dashboards (min-Distribution mit gebündeltem Node.js) zu
# einer mit vector/install.sh installierten OpenSearch, aus bereits
# heruntergeladenen Dateien. Wie install.sh: lädt nichts herunter, kein root,
# keine systemd-Unit, keine Dateien außerhalb von DASHBOARDS_DIR (Default
# BASE_DIR/dashboards). Gestartet wird über DASHBOARDS_DIR/bin/start.sh.
#
# Erwartete Dateien in ARTIFACT_DIR (auch eine Ebene tiefer, lädt download.sh
# mit DOWNLOAD_DASHBOARDS=true):
#   opensearch-dashboards-min-<V>-linux-<arch>.tar.gz   (+ optional .sha512 daneben)
#   securityDashboards-<V>.zip                          (nur mit users.sh)
#   indexManagementDashboards-<V>.zip                   (nur, wenn OpenSearch das Plugin
#                                                        opensearch-index-management hat)
#
# Auth/TLS: Hat users.sh Security eingeschaltet (BASE_DIR/bin/env.sh), installiert
# das Skript das Plugin securityDashboards, legt per Security-REST-API den
# Server-User DASHBOARDS_USER (Rolle kibana_server) an und schaltet HTTPS mit
# dem Node-Zertifikat ein. Angemeldet wird sich dann mit den Usern aus users.sh.
# Dafür muss OpenSearch laufen.
#
# Idempotent wie install.sh: Ein Versionswechsel installiert parallel nach
# DASHBOARDS_DIR/opensearch-dashboards-<version> und schwenkt DASHBOARDS_DIR/current um.
#
# Konfiguration: dieselbe install.env (siehe install.env.example).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
START_NODE=false
RESTART_NODE=false
ROTATE=false

usage() {
    cat <<EOF
Usage: $0 [--config <datei>] [--start] [--restart] [--rotate]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  --start           Dashboards starten (bzw. bei Änderungen neu starten) und warten, bis es antwortet
  --restart         Dashboards neu starten, falls es läuft und sich etwas geändert hat
  --rotate          Neues Passwort für den Server-User DASHBOARDS_USER erzeugen
  -h, --help        Diese Hilfe

Alle Einstellungen sind Variablen, siehe install.env.example.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
        --start) START_NODE=true; shift ;;
        --restart) RESTART_NODE=true; shift ;;
        --rotate) ROTATE=true; shift ;;
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
BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
DASHBOARDS_VERSION="${DASHBOARDS_VERSION:-}"            # leer = Version der installierten OpenSearch
SECURITY_DASHBOARDS_SHA512="${SECURITY_DASHBOARDS_SHA512:-}"
INDEX_MANAGEMENT_DASHBOARDS_SHA512="${INDEX_MANAGEMENT_DASHBOARDS_SHA512:-}"

DASHBOARDS_DIR="${DASHBOARDS_DIR:-$BASE_DIR/dashboards}"
DASHBOARDS_DIR="${DASHBOARDS_DIR%/}"
CONF_DIR="$DASHBOARDS_DIR/config"
DATA_DIR="$DASHBOARDS_DIR/data"
LOG_DIR="$DASHBOARDS_DIR/logs"
RUN_DIR="$DASHBOARDS_DIR/run"
BIN_DIR="$DASHBOARDS_DIR/bin"
SECRETS_ENV="$CONF_DIR/dashboards.env"
CERT_DIR="$BASE_DIR/config/certs"

NODE_NAME="${NODE_NAME:-$(hostname -s)}"
DASHBOARDS_HOST="${DASHBOARDS_HOST:-127.0.0.1}"
DASHBOARDS_PORT="${DASHBOARDS_PORT:-5601}"
DASHBOARDS_USER="${DASHBOARDS_USER:-kibanaserver}"       # Server-User, nur mit users.sh; muss kibana.server_username sein

# --- Hilfsfunktionen ----------------------------------------------------------

CHANGED=false

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

# Sucht genau eine Datei namens $1 in ARTIFACT_DIR (max. eine Ebene tief).
find_artifact() {
    local name="$1" found
    found="$(find "$ARTIFACT_DIR" -maxdepth 2 -type f -name "$name" 2>/dev/null | sort)"
    [ -n "$found" ] || die "$name nicht in $ARTIFACT_DIR gefunden — laden mit: DOWNLOAD_DASHBOARDS=true $SCRIPT_DIR/download.sh"
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
    tmp="$(mktemp "$CONF_DIR/.write.XXXXXX")"
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

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '%s' "$s"
}

gen_password() {
    # 32 Zeichen [A-Za-z0-9], erfüllt die Default-Passwortregeln der REST-API
    local pw=""
    while [ "${#pw}" -lt 32 ]; do
        pw="$pw$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9')"
    done
    printf '%s' "${pw:0:32}"
}

# api <METHOD> <PATH> [JSON per stdin] — mit Admin-Zertifikat; setzt RESP_STATUS/RESP_BODY.
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

[ "$(id -u)" -ne 0 ] || die "Nicht als root ausführen, sondern als der User von install.sh"
for cmd in curl tar sha512sum cmp find openssl setsid; do
    command -v "$cmd" >/dev/null || die "'$cmd' fehlt (dnf install -y curl tar coreutils diffutils findutils openssl util-linux)"
done

case "$(uname -m)" in
    x86_64) ARCH=x64 ;;
    aarch64) ARCH=arm64 ;;
    *) die "Nicht unterstützte Architektur: $(uname -m)" ;;
esac

[ -f "$BASE_DIR/bin/env.sh" ] || die "Keine OpenSearch unter $BASE_DIR, zuerst install.sh ausführen"
# OPENSEARCH_URL, OPENSEARCH_SECURITY und OPENSEARCH_CURL_OPTS (Admin-Zertifikat).
# shellcheck disable=SC1091
source "$BASE_DIR/bin/env.sh"
SECURITY="$OPENSEARCH_SECURITY"

mkdir -p "$DASHBOARDS_DIR" 2>/dev/null || true
[ -d "$DASHBOARDS_DIR" ] && [ -w "$DASHBOARDS_DIR" ] \
    || die "$DASHBOARDS_DIR existiert nicht oder ist nicht schreibbar. Einmalig als root: install -d -o $(id -un) -g $(id -gn) $DASHBOARDS_DIR"

# --- Dateien in ARTIFACT_DIR suchen -----------------------------------------

[ -d "$ARTIFACT_DIR" ] || die "ARTIFACT_DIR $ARTIFACT_DIR existiert nicht"

# Dashboards muss zur OpenSearch-Version passen (gleiche Major.Minor).
OS_VERSION="$(readlink "$BASE_DIR/current" 2>/dev/null | sed -E 's|.*/opensearch-||')"
if [ -z "$DASHBOARDS_VERSION" ]; then
    [ -n "$OS_VERSION" ] || die "$BASE_DIR/current fehlt, DASHBOARDS_VERSION setzen oder install.sh ausführen"
    DASHBOARDS_VERSION="$OS_VERSION"
fi
[ "${DASHBOARDS_VERSION%.*}" = "${OS_VERSION%.*}" ] \
    || warn "Dashboards $DASHBOARDS_VERSION passt nicht zu OpenSearch ${OS_VERSION:-?} (Major.Minor muss gleich sein)"

OSD_FILE="$(find_artifact "opensearch-dashboards-min-${DASHBOARDS_VERSION}-linux-${ARCH}.tar.gz")"
if [ "$SECURITY" = "true" ]; then
    SECURITY_FILE="$(find_artifact "securityDashboards-${DASHBOARDS_VERSION}.zip")"
fi
# Die Index-Verwaltung in Dashboards braucht das Plugin in OpenSearch.
ISM=false
if [ -d "$BASE_DIR/current/plugins/opensearch-index-management" ]; then
    ISM=true
    ISM_FILE="$(find_artifact "indexManagementDashboards-${DASHBOARDS_VERSION}.zip")"
fi

DIST_DIR="$DASHBOARDS_DIR/opensearch-dashboards-$DASHBOARDS_VERSION"
CURRENT="$DASHBOARDS_DIR/current"

# --- 1. Verzeichnisse ---------------------------------------------------------

mkdir -p "$BIN_DIR" "$CONF_DIR" "$DATA_DIR" "$LOG_DIR" "$RUN_DIR"
chmod 0750 "$CONF_DIR" "$DATA_DIR" "$LOG_DIR"

# --- 2. Prüfsummen ------------------------------------------------------------

verify_artifact "$OSD_FILE" ""
[ "$SECURITY" = "false" ] || verify_artifact "$SECURITY_FILE" "$SECURITY_DASHBOARDS_SHA512"
[ "$ISM" = "false" ] || verify_artifact "$ISM_FILE" "$INDEX_MANAGEMENT_DASHBOARDS_SHA512"

# --- 3. Entpacken -------------------------------------------------------------

if [ -x "$DIST_DIR/bin/opensearch-dashboards" ]; then
    log "OpenSearch Dashboards $DASHBOARDS_VERSION bereits entpackt: $DIST_DIR"
else
    log "Entpacke $OSD_FILE nach $DIST_DIR"
    rm -rf "$DIST_DIR"
    EXTRACT_TMP="$(mktemp -d "$DASHBOARDS_DIR/.extract-XXXXXX")"
    trap 'rm -rf "$EXTRACT_TMP"' EXIT
    tar -xzf "$OSD_FILE" -C "$EXTRACT_TMP"
    # Das Tarball-Verzeichnis heißt opensearch-dashboards-<V>-linux-<arch>.
    mv "$EXTRACT_TMP"/opensearch-dashboards-* "$DIST_DIR"
    rm -rf "$EXTRACT_TMP"
    trap - EXIT
    CHANGED=true
fi

# --- 4. Plugins --------------------------------------------------------------

# install_plugin <name> <zip>: installiert das Plugin, falls es fehlt oder das Zip
# sich geändert hat.
install_plugin() {
    local name="$1" file="$2" marker="$DIST_DIR/.$1.installed" id
    id="$(sha512sum "$file" | awk '{print $1}')"
    if [ -f "$marker" ] && [ "$(cat "$marker")" = "$id" ] && [ -d "$DIST_DIR/plugins/$name" ]; then
        log "Plugin $name bereits installiert"
        return
    fi
    log "Installiere Plugin $name aus $file"
    rm -rf "${DIST_DIR:?}/plugins/$name" "$marker"
    "$DIST_DIR/bin/opensearch-dashboards-plugin" install --quiet "file://$file"
    echo "$id" >"$marker"
    CHANGED=true
}

# remove_plugin <name> <grund>
remove_plugin() {
    [ -d "$DIST_DIR/plugins/$1" ] || return 0
    log "Entferne Plugin $1 ($2)"
    rm -rf "${DIST_DIR:?}/plugins/$1" "$DIST_DIR/.$1.installed"
    CHANGED=true
}

if [ "$SECURITY" = "true" ]; then
    install_plugin securityDashboards "$SECURITY_FILE"
else
    # Ohne Security-Plugin in OpenSearch startet Dashboards mit dem Plugin nicht.
    remove_plugin securityDashboards "OpenSearch läuft ohne Security"
fi

if [ "$ISM" = "true" ]; then
    install_plugin indexManagementDashboards "$ISM_FILE"
else
    remove_plugin indexManagementDashboards "OpenSearch ohne opensearch-index-management"
fi

# --- 5. Server-User (nur mit Security) ----------------------------------------

if [ "$SECURITY" = "true" ]; then
    RESP_FILE="$(mktemp "$CONF_DIR/.resp.XXXXXX")"
    trap 'rm -f "$RESP_FILE"' EXIT

    OSD_SERVER_PASSWORD=""
    OSD_COOKIE_PASSWORD=""
    if [ -f "$SECRETS_ENV" ]; then
        # shellcheck disable=SC1090
        source "$SECRETS_ENV"
    fi
    [ "$ROTATE" = "false" ] || OSD_SERVER_PASSWORD=""
    OSD_SERVER_PASSWORD="${OSD_SERVER_PASSWORD:-$(gen_password)}"
    OSD_COOKIE_PASSWORD="${OSD_COOKIE_PASSWORD:-$(gen_password)}"
    # Vor den API-Aufrufen speichern, damit ein Abbruch kein Passwort verliert.
    {
        echo "# Verwaltet von vector/dashboards.sh"
        printf 'OSD_SERVER_PASSWORD=%q\n' "$OSD_SERVER_PASSWORD"
        printf 'OSD_COOKIE_PASSWORD=%q\n' "$OSD_COOKIE_PASSWORD"
    } | write_file "$SECRETS_ENV" 0600

    api GET "/_plugins/_security/health" </dev/null
    [ "$RESP_STATUS" = "200" ] \
        || die "OpenSearch unter $OPENSEARCH_URL nicht erreichbar (HTTP $RESP_STATUS) — erst $BASE_DIR/bin/start.sh"

    # Zugriffe auf den Index .kibana erlaubt das Security-Plugin nur dem User aus
    # config.dynamic.kibana.server_username (Default kibanaserver), unabhängig
    # von der Rolle kibana_server. Per REST-API lässt sich das nicht ändern.
    api GET "/_plugins/_security/api/securityconfig" </dev/null
    api_ok "Lesen der Security-Konfiguration"
    server_username="$(tr -d ' \n' <<<"$RESP_BODY" | grep -o '"server_username":"[^"]*"' | cut -d'"' -f4)"
    [ "$server_username" = "$DASHBOARDS_USER" ] \
        || die "DASHBOARDS_USER=$DASHBOARDS_USER, das Security-Plugin erwartet als Server-User aber ${server_username:-?} (config.yml: kibana.server_username) — DASHBOARDS_USER=${server_username:-kibanaserver} setzen"

    # Der Server-User verwaltet nur die Dashboards-Indizes; die Anmeldung im
    # Browser läuft mit den eigenen Usern (z.B. admin aus users.sh).
    log "Lege Server-User $DASHBOARDS_USER an (kibana_server)"
    api PUT "/_plugins/_security/api/internalusers/$DASHBOARDS_USER" <<EOF
{"password": "$(json_escape "$OSD_SERVER_PASSWORD")", "backend_roles": [],
 "description": "Server-User von OpenSearch Dashboards (vector/dashboards.sh)"}
EOF
    api_ok "Anlegen von $DASHBOARDS_USER"

    # Mapping komplett per PUT schreiben (PATCH lehnt das Plugin bei leerem
    # users ab), vorhandene Einträge bleiben erhalten.
    api GET "/_plugins/_security/api/rolesmapping/kibana_server" </dev/null
    if ! grep -q "\"$DASHBOARDS_USER\"" <<<"$RESP_BODY"; then
        mapping_array() {
            local v
            v="$(tr -d '\n' <<<"$RESP_BODY" | grep -o "\"$1\":\[[^]]*\]" | sed "s/^\"$1\"://")" || true
            echo "${v:-[]}"
        }
        users="$(mapping_array users)"
        user_json="\"$(json_escape "$DASHBOARDS_USER")\""
        if [ "$(tr -d ' ' <<<"$users")" = "[]" ]; then users="[$user_json]"; else users="${users%]},$user_json]"; fi
        api PUT "/_plugins/_security/api/rolesmapping/kibana_server" <<EOF
{"users": $users, "backend_roles": $(mapping_array backend_roles), "hosts": $(mapping_array hosts),
 "description": "vector/dashboards.sh"}
EOF
        api_ok "Rollen-Mapping kibana_server"
    fi
fi

# --- 6. Konfiguration ---------------------------------------------------------

# Der Launcher liest node.options nur aus OSD_PATH_CONF. Einmalig aus der
# Distribution übernehmen, danach gehört die Datei dem Betreiber (z.B.
# --max-old-space-size).
[ -f "$CONF_DIR/node.options" ] || write_file "$CONF_DIR/node.options" 0640 <"$DIST_DIR/config/node.options"

if [ "$SECURITY" = "true" ]; then
    SCHEME=https
else
    SCHEME=http
fi

{
    cat <<EOF
# Verwaltet von vector/dashboards.sh — Änderungen hier werden beim nächsten Lauf
# überschrieben. Eigene Einstellungen in $CONF_DIR/opensearch_dashboards.local.yml
# eintragen (wird unten angehängt) oder die Variablen in install.env ändern.
server.host: "$DASHBOARDS_HOST"
server.port: $DASHBOARDS_PORT
server.name: "$NODE_NAME"
path.data: $DATA_DIR
logging.dest: $LOG_DIR/opensearch-dashboards.log
opensearch.hosts: ["$OPENSEARCH_URL"]

# Features: Multiple Data Sources, Workspaces, Explore
data_source.enabled: true
workspace.enabled: true
explore.enabled: true
EOF
    if [ "$SECURITY" = "true" ]; then
        cat <<EOF

# Auth/TLS (users.sh): Node-Zertifikat auch für HTTPS von Dashboards
server.ssl.enabled: true
server.ssl.certificate: $CERT_DIR/node.pem
server.ssl.key: $CERT_DIR/node-key.pem
opensearch.ssl.verificationMode: full
opensearch.ssl.certificateAuthorities: ["$CERT_DIR/root-ca.pem"]
opensearch.username: "$DASHBOARDS_USER"
opensearch.password: "$OSD_SERVER_PASSWORD"
opensearch.requestHeadersAllowlist: ["authorization", "securitytenant"]
opensearch_security.multitenancy.enabled: false
opensearch_security.cookie.secure: true
opensearch_security.cookie.password: "$OSD_COOKIE_PASSWORD"
EOF
    fi
    if [ -f "$CONF_DIR/opensearch_dashboards.local.yml" ]; then
        echo
        echo "# --- aus $CONF_DIR/opensearch_dashboards.local.yml ---"
        cat "$CONF_DIR/opensearch_dashboards.local.yml"
    fi
} | write_file "$CONF_DIR/opensearch_dashboards.yml" 0600

# --- 7. Symlink auf die aktive Version ---------------------------------------

if [ "$(readlink "$CURRENT" 2>/dev/null)" != "$DIST_DIR" ]; then
    log "Setze $CURRENT -> $DIST_DIR"
    ln -sfn "$DIST_DIR" "$CURRENT"
    CHANGED=true
fi

# --- 8. Start-/Stop-Skripte ---------------------------------------------------

HEALTH_HOST="$DASHBOARDS_HOST"
case "$HEALTH_HOST" in 0.0.0.0|::|localhost) HEALTH_HOST=127.0.0.1 ;; esac
if [ "$SECURITY" = "true" ]; then
    CURL_OPTS="(--cacert \"$CERT_DIR/root-ca.pem\")"
else
    CURL_OPTS="()"
fi

write_file "$BIN_DIR/env.sh" 0644 <<EOF
# Verwaltet von vector/dashboards.sh — wird von start.sh/stop.sh/status.sh gelesen.
export OSD_HOME="$CURRENT"
export OSD_PATH_CONF="$CONF_DIR"
DASHBOARDS_PIDFILE="$RUN_DIR/opensearch-dashboards.pid"
DASHBOARDS_URL="$SCHEME://$HEALTH_HOST:$DASHBOARDS_PORT"
DASHBOARDS_SECURITY=$SECURITY
DASHBOARDS_CURL_OPTS=$CURL_OPTS
DASHBOARDS_SECRETS="$SECRETS_ENV"
DASHBOARDS_SERVER_USER="$DASHBOARDS_USER"
DASHBOARDS_LOG="$LOG_DIR/opensearch-dashboards.log"
DASHBOARDS_STARTUP_LOG="$LOG_DIR/startup.log"

dashboards_pid() {
    local pid
    [ -f "\$DASHBOARDS_PIDFILE" ] || return 1
    pid="\$(cat "\$DASHBOARDS_PIDFILE")"
    [ -n "\$pid" ] && kill -0 "\$pid" 2>/dev/null && echo "\$pid"
}
EOF

write_file "$BIN_DIR/start.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/dashboards.sh — startet OpenSearch Dashboards im
# Hintergrund und wartet, bis die HTTP-Schnittstelle antwortet.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if pid="$(dashboards_pid)"; then
    echo "OpenSearch Dashboards läuft bereits (PID $pid)"
    exit 0
fi

cd "$OSD_HOME"
# Eigene Session, damit Strg-C oder das Ende der SSH-Sitzung Dashboards nicht
# mitbeendet. setsid und bin/opensearch-dashboards ersetzen sich per exec durch
# node, $! ist also dessen PID.
setsid "$OSD_HOME/bin/opensearch-dashboards" </dev/null >"$DASHBOARDS_STARTUP_LOG" 2>&1 &
echo $! >"$DASHBOARDS_PIDFILE"

echo "Warte auf $DASHBOARDS_URL ..."
for _ in $(seq 1 60); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "${DASHBOARDS_CURL_OPTS[@]}" "$DASHBOARDS_URL/api/status" 2>/dev/null)" || true
    # 503 = "server is not ready yet"; mit Security verlangt /api/status Login (401).
    case "$code" in
        200|401)
            echo "OpenSearch Dashboards läuft (PID $(dashboards_pid))"
            exit 0
            ;;
    esac
    if ! dashboards_pid >/dev/null; then
        tail -n 30 "$DASHBOARDS_STARTUP_LOG" >&2
        echo "FEHLER: OpenSearch Dashboards hat sich beendet, siehe $DASHBOARDS_STARTUP_LOG und $DASHBOARDS_LOG" >&2
        exit 1
    fi
    sleep 3
done
echo "FEHLER: $DASHBOARDS_URL antwortet nicht, siehe $DASHBOARDS_LOG" >&2
exit 1
EOF

write_file "$BIN_DIR/stop.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/dashboards.sh — beendet OpenSearch Dashboards per SIGTERM.
set -euo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if ! pid="$(dashboards_pid)"; then
    echo "OpenSearch Dashboards läuft nicht"
    rm -f "$DASHBOARDS_PIDFILE"
    exit 0
fi

echo "Stoppe OpenSearch Dashboards (PID $pid) ..."
kill "$pid"
for _ in $(seq 1 60); do
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f "$DASHBOARDS_PIDFILE"
        echo "OpenSearch Dashboards gestoppt"
        exit 0
    fi
    sleep 1
done
echo "FEHLER: OpenSearch Dashboards (PID $pid) läuft nach 60s noch — ggf. kill -9 $pid" >&2
exit 1
EOF

write_file "$BIN_DIR/status.sh" 0755 <<'EOF'
#!/usr/bin/env bash
# Verwaltet von vector/dashboards.sh — zeigt Prozess- und Dashboards-Status.
set -uo pipefail
# shellcheck disable=SC1091
source "$(dirname "$(readlink -f "$0")")/env.sh"

if pid="$(dashboards_pid)"; then
    echo "Prozess:  läuft (PID $pid)"
else
    echo "Prozess:  läuft nicht"
    exit 3
fi
auth=()
if [ "$DASHBOARDS_SECURITY" = "true" ]; then
    # shellcheck disable=SC1090
    source "$DASHBOARDS_SECRETS"
    auth=(-K -)
fi
# Zugangsdaten per stdin an curl, damit sie nicht in der Prozessliste stehen.
status="$(printf 'user = "%s:%s"\n' "$DASHBOARDS_SERVER_USER" "${OSD_SERVER_PASSWORD:-}" \
    | curl -fs "${auth[@]}" "${DASHBOARDS_CURL_OPTS[@]}" "$DASHBOARDS_URL/api/status")" \
    || { echo "HTTP:     $DASHBOARDS_URL antwortet nicht bzw. ist nicht bereit"; exit 1; }
echo "URL:      $DASHBOARDS_URL"
echo "Status:   $(grep -o '"overall":{[^}]*"state":"[a-z]*"' <<<"$status" | grep -o '"state":"[a-z]*"' | cut -d'"' -f4)"
EOF

# --- 9. Hinweise --------------------------------------------------------------

case "$DASHBOARDS_HOST" in
    127.0.0.1|localhost|::1) ;;
    *)
        [ "$SECURITY" = "true" ] \
            || warn "DASHBOARDS_HOST=$DASHBOARDS_HOST ohne users.sh: Dashboards ist ohne Login für jeden erreichbar, der den Port erreicht"
        ;;
esac

# --- 10. Starten / Neustarten -------------------------------------------------

RUNNING=false
if [ -f "$RUN_DIR/opensearch-dashboards.pid" ] && kill -0 "$(cat "$RUN_DIR/opensearch-dashboards.pid")" 2>/dev/null; then
    RUNNING=true
fi

if [ "$RUNNING" = "true" ] && [ "$CHANGED" = "true" ]; then
    if [ "$START_NODE" = "true" ] || [ "$RESTART_NODE" = "true" ]; then
        log "Starte OpenSearch Dashboards neu (Änderungen)"
        "$BIN_DIR/stop.sh"
        "$BIN_DIR/start.sh"
    else
        warn "Es gab Änderungen, Dashboards läuft noch mit dem alten Stand: $BIN_DIR/stop.sh && $BIN_DIR/start.sh"
    fi
elif [ "$RUNNING" = "false" ] && [ "$START_NODE" = "true" ]; then
    "$BIN_DIR/start.sh"
fi

if [ "$SECURITY" = "true" ]; then
    LOGIN="mit den Usern aus users.sh (admin: $BASE_DIR/config/users.env)"
else
    LOGIN="ohne Login (kein users.sh)"
fi

cat <<EOF

Fertig: OpenSearch Dashboards $DASHBOARDS_VERSION unter $DASHBOARDS_DIR
  URL:           $SCHEME://$HEALTH_HOST:$DASHBOARDS_PORT, Anmeldung $LOGIN
  OpenSearch:    $OPENSEARCH_URL
  Binaries:      $CURRENT -> $DIST_DIR
  Konfiguration: $CONF_DIR (eigene Settings: opensearch_dashboards.local.yml, node.options)
  Daten / Logs:  $DATA_DIR / $LOG_DIR

Starten:   $BIN_DIR/start.sh
Stoppen:   $BIN_DIR/stop.sh
Status:    $BIN_DIR/status.sh
EOF
