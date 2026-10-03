#!/usr/bin/env bash
# Schaltet Auth und TLS für eine mit vector/install.sh installierte OpenSearch ein
# und legt drei User an:
#   ADMIN_USER (Default admin)   darf alles (all_access + Security-REST-API)
#   AGENT_USER (Default agent)   darf in AGENT_INDEX_PATTERNS Indizes und Mappings
#                                anlegen, Dokumente/Vektoren schreiben, ändern,
#                                löschen und suchen, aber keine Indizes löschen
#                                (für die Ingest-Pipeline)
#   SEARCH_USER (Default search) darf in SEARCH_INDEX_PATTERNS nur suchen und
#                                lesen (für Agents, die Handbücher durchsuchen)
#
# Ablauf (jeder Schritt wird übersprungen, wenn er schon erledigt ist):
#   1. Zertifikate unter BASE_DIR/config/certs: eigene CA, Node-Zertifikat
#      (Transport + HTTPS) und Admin-Zertifikat für die Security-Verwaltung
#   2. Security-Konfiguration für die Erstinitialisierung unter
#      BASE_DIR/config/opensearch-security/ und die Settings in
#      BASE_DIR/config/opensearch.security.yml
#   3. install.sh --start: installiert das Security-Plugin
#      (opensearch-security-<V>.0.zip aus ARTIFACT_DIR) und startet neu
#   4. User, Rolle und Rollen-Mapping per Security-REST-API (mit Admin-Zertifikat),
#      Passwörter landen in BASE_DIR/config/users.env (0600)
#
# Erneutes Ausführen setzt die Passwörter aus users.env erneut, mit --rotate
# werden neue erzeugt. Andere, z.B. per adduser.sh angelegte User bleiben erhalten.
#
# Als derselbe User wie install.sh ausführen, install.sh muss daneben liegen.
# Konfiguration: dieselbe install.env (siehe install.env.example).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CONFIG_FILE="$SCRIPT_DIR/install.env"
ROTATE=false

usage() {
    cat <<EOF
Usage: $0 [--config <datei>] [--rotate]

  --config <datei>  Variablen-Datei (Default: $SCRIPT_DIR/install.env, falls vorhanden)
  --rotate          Neue Passwörter für alle drei User erzeugen
  -h, --help        Diese Hilfe

Feste Passwörter statt generierter: ADMIN_PASSWORD / AGENT_PASSWORD /
SEARCH_PASSWORD als Umgebungsvariable setzen. Weitere Variablen siehe install.env.example.
EOF
}

while [ $# -gt 0 ]; do
    case "$1" in
        --config) CONFIG_FILE="$2"; shift 2 ;;
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

# --- Einstellungen (Defaults, wie in install.sh) -------------------------------

ARTIFACT_DIR="${ARTIFACT_DIR:-$HOME}"
BASE_DIR="${BASE_DIR:-/opt/local/opensearch}"
BASE_DIR="${BASE_DIR%/}"
CONF_DIR="$BASE_DIR/config"
BIN_DIR="$BASE_DIR/bin"
CERT_DIR="$CONF_DIR/certs"
SEC_DIR="$CONF_DIR/opensearch-security"
USERS_ENV="$CONF_DIR/users.env"

CLUSTER_NAME="${CLUSTER_NAME:-opensearch-vector}"
NODE_NAME="${NODE_NAME:-$(hostname -s)}"
NETWORK_HOST="${NETWORK_HOST:-127.0.0.1}"
HTTP_PORT="${HTTP_PORT:-9200}"

ADMIN_USER="${ADMIN_USER:-admin}"
AGENT_USER="${AGENT_USER:-agent}"
AGENT_ROLE="${AGENT_ROLE:-vector_agent}"
AGENT_INDEX_PATTERNS="${AGENT_INDEX_PATTERNS:-*}"   # Komma-getrennt, z.B. "vectors-*,rag-*"
SEARCH_USER="${SEARCH_USER:-search}"
SEARCH_ROLE="${SEARCH_ROLE:-vector_search}"
SEARCH_INDEX_PATTERNS="${SEARCH_INDEX_PATTERNS:-$AGENT_INDEX_PATTERNS}"
CERT_DAYS="${CERT_DAYS:-3650}"
EXTRA_SANS="${EXTRA_SANS:-}"                        # weitere Hostnamen/IPs fürs Node-Zertifikat
# Feste Passwörter nur als Umgebungsvariable, nicht in install.env eintragen.
ADMIN_PASSWORD_ARG="${ADMIN_PASSWORD:-}"
AGENT_PASSWORD_ARG="${AGENT_PASSWORD:-}"
SEARCH_PASSWORD_ARG="${SEARCH_PASSWORD:-}"

# --- Hilfsfunktionen ----------------------------------------------------------

log() { echo "==> $*"; }
warn() { echo "WARN: $*" >&2; }
die() { echo "FEHLER: $*" >&2; exit 1; }

json_escape() {
    local s="$1"
    s="${s//\\/\\\\}"
    s="${s//\"/\\\"}"
    printf '%s' "$s"
}

json_list() {
    # "a, b,c" -> ["a","b","c"]; set -f, damit "*" nicht als Glob expandiert
    local - IFS=',' item out=""
    set -f
    for item in $1; do
        item="$(echo "$item" | xargs)"
        [ -n "$item" ] && out="${out:+$out,}\"$(json_escape "$item")\""
    done
    echo "[$out]"
}

gen_password() {
    # 32 Zeichen [A-Za-z0-9], erfüllt die Default-Passwortregeln der REST-API
    local pw=""
    while [ "${#pw}" -lt 32 ]; do
        pw="$pw$(openssl rand -base64 48 | tr -dc 'A-Za-z0-9')"
    done
    printf '%s' "${pw:0:32}"
}

# Schreibt stdin nach dest, falls sich der Inhalt unterscheidet.
write_file() {
    local dest="$1" mode="$2" tmp
    tmp="$(mktemp "$CONF_DIR/.write.XXXXXX")"
    cat >"$tmp"
    if [ -f "$dest" ] && cmp -s "$tmp" "$dest"; then
        rm -f "$tmp"
    else
        log "Schreibe $dest"
        mv -f "$tmp" "$dest"
    fi
    chmod "$mode" "$dest"
}

# sign_cert <name> <subject> <extensions>: Key + von der CA signiertes Zertifikat.
# Keys im PKCS#8-Format, wie das Security-Plugin sie verlangt.
sign_cert() {
    local name="$1" subj="$2" ext="$3"
    (umask 0077 && openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:2048 -out "$CERT_DIR/$name-key.pem")
    openssl req -new -key "$CERT_DIR/$name-key.pem" -subj "$subj" -out "$CERT_DIR/$name.csr"
    openssl x509 -req -in "$CERT_DIR/$name.csr" -CA "$CERT_DIR/root-ca.pem" -CAkey "$CERT_DIR/root-ca-key.pem" \
        -set_serial "0x$(openssl rand -hex 16)" -days "$CERT_DAYS" -sha256 \
        -extfile <(printf '%s\n' "$ext") -out "$CERT_DIR/$name.pem" 2>/dev/null
    rm -f "$CERT_DIR/$name.csr"
    chmod 0644 "$CERT_DIR/$name.pem"
}

# api <METHOD> <PATH> [JSON per stdin] — mit Admin-Zertifikat; setzt RESP_STATUS/RESP_BODY.
api() {
    local method="$1" path="$2" data=()
    [ "$method" = "GET" ] || data=(-H 'Content-Type: application/json' --data-binary @-)
    RESP_STATUS="$(curl -s -o "$RESP_FILE" -w '%{http_code}' -X "$method" "${ADMIN_CURL[@]}" \
        "${data[@]}" "$URL$path")" || RESP_STATUS="000"
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
for cmd in curl openssl cmp; do
    command -v "$cmd" >/dev/null || die "'$cmd' fehlt (dnf install -y curl openssl diffutils)"
done
[ -x "$SCRIPT_DIR/install.sh" ] || die "$SCRIPT_DIR/install.sh fehlt, users.sh muss daneben liegen"
[ -x "$BASE_DIR/current/bin/opensearch" ] && [ -f "$BIN_DIR/env.sh" ] \
    || die "Keine Installation unter $BASE_DIR, zuerst install.sh ausführen"
find "$ARTIFACT_DIR" -maxdepth 2 -type f -name 'opensearch-security-*.zip' 2>/dev/null | grep -q . \
    || die "Kein opensearch-security-<version>.zip in $ARTIFACT_DIR — laden mit: DOWNLOAD_SECURITY=true $SCRIPT_DIR/download.sh"

mkdir -p "$CERT_DIR" "$SEC_DIR"
chmod 0750 "$CERT_DIR" "$SEC_DIR"

WORKDIR="$(mktemp -d "$CONF_DIR/.users-XXXXXX")"
trap 'rm -rf "$WORKDIR"' EXIT
RESP_FILE="$WORKDIR/resp.json"

# --- 1. Zertifikate -----------------------------------------------------------

CERT_ORG="$CLUSTER_NAME"
NODE_DN_PATTERN="CN=*,OU=node,O=$CERT_ORG"
ADMIN_DN="CN=admin,OU=admin,O=$CERT_ORG"

# Bei Mehrknoten-Clustern müssen alle Knoten dieselbe CA nutzen: root-ca.pem und
# root-ca-key.pem vom ersten Knoten vorher nach config/certs/ kopieren.
if [ ! -f "$CERT_DIR/root-ca.pem" ]; then
    log "Erzeuge CA $CERT_DIR/root-ca.pem"
    (umask 0077 && openssl genpkey -quiet -algorithm RSA -pkeyopt rsa_keygen_bits:3072 -out "$CERT_DIR/root-ca-key.pem")
    openssl req -x509 -new -key "$CERT_DIR/root-ca-key.pem" -sha256 -days "$CERT_DAYS" \
        -subj "/O=$CERT_ORG/OU=ca/CN=$CERT_ORG Root CA" \
        -addext "basicConstraints=critical,CA:TRUE" \
        -addext "keyUsage=critical,keyCertSign,cRLSign" \
        -out "$CERT_DIR/root-ca.pem"
    chmod 0644 "$CERT_DIR/root-ca.pem"
    rm -f "$CERT_DIR/node.pem" "$CERT_DIR/admin.pem"
fi

# SANs: alles, worüber Clients den Node ansprechen. Ändern sie sich (z.B. neue
# NETWORK_HOST), wird das Node-Zertifikat neu ausgestellt.
SAN="DNS:localhost,IP:127.0.0.1,IP:::1,DNS:$(hostname -s)"
FQDN="$(hostname -f 2>/dev/null || true)"
[ -n "$FQDN" ] && [ "$FQDN" != "$(hostname -s)" ] && SAN="$SAN,DNS:$FQDN"
case "$NETWORK_HOST" in
    127.0.0.1|localhost|::1|0.0.0.0|_*_) ;;
    *:*) SAN="$SAN,IP:$NETWORK_HOST" ;;
    *[!0-9.]*) SAN="$SAN,DNS:$NETWORK_HOST" ;;
    *) SAN="$SAN,IP:$NETWORK_HOST" ;;
esac
for host in ${EXTRA_SANS//,/ }; do
    case "$host" in *:*) SAN="$SAN,IP:$host" ;; *[!0-9.]*) SAN="$SAN,DNS:$host" ;; *) SAN="$SAN,IP:$host" ;; esac
done
NODE_SUBJ="/O=$CERT_ORG/OU=node/CN=$NODE_NAME"

if [ ! -f "$CERT_DIR/node.pem" ] || [ "$(cat "$CERT_DIR/node.san" 2>/dev/null)" != "$NODE_SUBJ $SAN" ]; then
    log "Erzeuge Node-Zertifikat ($SAN)"
    sign_cert node "$NODE_SUBJ" "basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=serverAuth,clientAuth
subjectAltName=$SAN"
    echo "$NODE_SUBJ $SAN" >"$CERT_DIR/node.san"
fi

if [ ! -f "$CERT_DIR/admin.pem" ]; then
    log "Erzeuge Admin-Zertifikat ($ADMIN_DN)"
    sign_cert admin "/O=$CERT_ORG/OU=admin/CN=admin" "basicConstraints=CA:FALSE
keyUsage=critical,digitalSignature,keyEncipherment
extendedKeyUsage=clientAuth"
fi

for f in node admin; do
    openssl x509 -checkend $((30 * 86400)) -noout -in "$CERT_DIR/$f.pem" >/dev/null \
        || warn "$CERT_DIR/$f.pem läuft in weniger als 30 Tagen ab — löschen und users.sh erneut ausführen"
done

# --- 2. Security-Konfiguration -------------------------------------------------

# Nur für die Erstinitialisierung des Security-Index. Danach gilt der Index,
# Änderungen laufen über die REST-API (siehe unten) oder securityadmin.sh.
write_file "$SEC_DIR/config.yml" 0640 <<'EOF'
# Verwaltet von vector/users.sh, nur für die Erstinitialisierung
_meta:
  type: "config"
  config_version: 2
config:
  dynamic:
    http:
      anonymous_auth_enabled: false
    authc:
      basic_internal_auth_domain:
        description: "Basic-Auth gegen die internen User"
        http_enabled: true
        transport_enabled: true
        order: 0
        http_authenticator:
          type: basic
          challenge: true
        authentication_backend:
          type: intern
EOF
# Keine Demo-User: Die User legt users.sh unten per REST-API an.
for kind in internalusers:internal_users roles:roles actiongroups:action_groups tenants:tenants; do
    write_file "$SEC_DIR/${kind#*:}.yml" 0640 <<EOF
# Verwaltet von vector/users.sh, nur für die Erstinitialisierung
_meta:
  type: "${kind%%:*}"
  config_version: 2
EOF
done
write_file "$SEC_DIR/roles_mapping.yml" 0640 <<'EOF'
# Verwaltet von vector/users.sh, nur für die Erstinitialisierung
_meta:
  type: "rolesmapping"
  config_version: 2
all_access:
  reserved: false
  backend_roles: ["admin"]
  description: "User mit Backend-Rolle admin dürfen alles"
security_rest_api_access:
  reserved: false
  backend_roles: ["admin"]
  description: "User mit Backend-Rolle admin dürfen die Security-REST-API nutzen"
EOF

# Pfade relativ zu config/. Admin-Zertifikat: darf die Security-Konfiguration
# verwalten, auch wenn kein User mehr funktioniert.
write_file "$CONF_DIR/opensearch.security.yml" 0640 <<EOF
# Verwaltet von vector/users.sh, wird von install.sh an opensearch.yml angehängt.
plugins.security.ssl.transport.pemcert_filepath: certs/node.pem
plugins.security.ssl.transport.pemkey_filepath: certs/node-key.pem
plugins.security.ssl.transport.pemtrustedcas_filepath: certs/root-ca.pem
plugins.security.ssl.transport.enforce_hostname_verification: false
plugins.security.ssl.http.enabled: true
plugins.security.ssl.http.pemcert_filepath: certs/node.pem
plugins.security.ssl.http.pemkey_filepath: certs/node-key.pem
plugins.security.ssl.http.pemtrustedcas_filepath: certs/root-ca.pem
plugins.security.authcz.admin_dn: ["$ADMIN_DN"]
plugins.security.nodes_dn: ["$NODE_DN_PATTERN"]
plugins.security.allow_default_init_securityindex: true
plugins.security.restapi.roles_enabled: ["all_access", "security_rest_api_access"]
plugins.security.check_snapshot_restore_write_privileges: true
plugins.security.enable_snapshot_restore_privilege: true
plugins.security.system_indices.enabled: true
EOF

# --- 3. Plugin installieren, (neu) starten ------------------------------------

log "Rufe install.sh auf (installiert Security-Plugin, startet bei Änderungen neu)"
"$SCRIPT_DIR/install.sh" --config "$CONFIG_FILE" --start

# shellcheck disable=SC1091
source "$BIN_DIR/env.sh"
URL="$OPENSEARCH_URL"
ADMIN_CURL=("${OPENSEARCH_CURL_OPTS[@]}")
[ "$OPENSEARCH_SECURITY" = "true" ] || die "install.sh hat Security nicht aktiviert ($BIN_DIR/env.sh)"

# Liefert der Node nicht das aktuelle node.pem aus (neues Zertifikat, oder er
# läuft noch ohne TLS, weil install.sh früher ohne --restart lief): neu starten.
cert_fp() { openssl x509 -noout -fingerprint -sha256 2>/dev/null; }
HOSTPORT="${URL#https://}"
SERVED_FP="$(openssl s_client -connect "$HOSTPORT" </dev/null 2>/dev/null | cert_fp)" || true
if [ "$SERVED_FP" != "$(cert_fp <"$CERT_DIR/node.pem")" ]; then
    log "Starte OpenSearch neu, damit Zertifikate/Security greifen"
    "$BIN_DIR/stop.sh"
    "$BIN_DIR/start.sh"
fi

log "Warte auf Initialisierung des Security-Index ..."
for i in $(seq 1 60); do
    api GET "/_plugins/_security/health" </dev/null
    [ "$RESP_STATUS" = "200" ] && break
    [ "$i" -lt 60 ] || die "Security nicht initialisiert (HTTP $RESP_STATUS): $RESP_BODY — siehe $OPENSEARCH_LOG"
    sleep 2
done

# --- 4. Passwörter ------------------------------------------------------------

OPENSEARCH_ADMIN_PASSWORD=""
OPENSEARCH_AGENT_PASSWORD=""
OPENSEARCH_SEARCH_PASSWORD=""
if [ -f "$USERS_ENV" ] && [ "$ROTATE" = "false" ]; then
    # shellcheck disable=SC1090
    source "$USERS_ENV"
fi
ADMIN_PW="${ADMIN_PASSWORD_ARG:-${OPENSEARCH_ADMIN_PASSWORD:-$(gen_password)}}"
AGENT_PW="${AGENT_PASSWORD_ARG:-${OPENSEARCH_AGENT_PASSWORD:-$(gen_password)}}"
SEARCH_PW="${SEARCH_PASSWORD_ARG:-${OPENSEARCH_SEARCH_PASSWORD:-$(gen_password)}}"

# Vor den API-Aufrufen speichern, damit ein Abbruch kein Passwort verliert.
{
    echo "# Verwaltet von vector/users.sh — Zugangsdaten für $URL"
    printf 'OPENSEARCH_URL=%q\n' "$URL"
    printf 'OPENSEARCH_CACERT=%q\n' "$CERT_DIR/root-ca.pem"
    printf 'OPENSEARCH_ADMIN_USER=%q\n' "$ADMIN_USER"
    printf 'OPENSEARCH_ADMIN_PASSWORD=%q\n' "$ADMIN_PW"
    printf 'OPENSEARCH_AGENT_USER=%q\n' "$AGENT_USER"
    printf 'OPENSEARCH_AGENT_PASSWORD=%q\n' "$AGENT_PW"
    printf 'OPENSEARCH_SEARCH_USER=%q\n' "$SEARCH_USER"
    printf 'OPENSEARCH_SEARCH_PASSWORD=%q\n' "$SEARCH_PW"
} | write_file "$USERS_ENV" 0600

# --- 5. User, Rolle, Mapping --------------------------------------------------

log "Lege User $ADMIN_USER an (all_access)"
api PUT "/_plugins/_security/api/internalusers/$ADMIN_USER" <<EOF
{"password": "$(json_escape "$ADMIN_PW")", "backend_roles": ["admin"],
 "description": "Admin, darf alles (vector/users.sh)"}
EOF
api_ok "Anlegen von $ADMIN_USER"

# Admin-Rechte zusätzlich direkt am User, falls das Mapping der Backend-Rolle
# admin im Security-Index fehlt (z.B. anders initialisiert).
for role in all_access security_rest_api_access; do
    api GET "/_plugins/_security/api/rolesmapping/$role" </dev/null
    if [ "$RESP_STATUS" = "404" ]; then
        api PUT "/_plugins/_security/api/rolesmapping/$role" <<EOF
{"backend_roles": ["admin"], "users": ["$(json_escape "$ADMIN_USER")"]}
EOF
        api_ok "Rollen-Mapping $role"
    elif ! grep -q "\"$ADMIN_USER\"\|\"admin\"" <<<"$RESP_BODY"; then
        api PATCH "/_plugins/_security/api/rolesmapping/$role" <<EOF
[{"op": "add", "path": "/users/-", "value": "$(json_escape "$ADMIN_USER")"}]
EOF
        api_ok "Rollen-Mapping $role"
    fi
done

# Vektor-Daten lesen und schreiben: Indizes/Mappings anlegen, Dokumente
# indexieren, ändern, löschen (auch per _delete_by_query), suchen inkl. k-NN.
# Indizes löschen oder Settings ändern darf der Agent nicht.
log "Lege Rolle $AGENT_ROLE an (Index-Muster: $AGENT_INDEX_PATTERNS)"
api PUT "/_plugins/_security/api/roles/$AGENT_ROLE" <<EOF
{
  "description": "Vektor-Daten lesen und schreiben (vector/users.sh)",
  "cluster_permissions": [
    "cluster_composite_ops",
    "cluster_monitor",
    "indices:data/read/scroll*",
    "indices:data/read/search/template*"
  ],
  "index_permissions": [{
    "index_patterns": $(json_list "$AGENT_INDEX_PATTERNS"),
    "allowed_actions": [
      "crud",
      "create_index",
      "indices:admin/mappings/get",
      "indices:admin/get",
      "indices:admin/refresh*",
      "indices:admin/aliases/get",
      "indices_monitor"
    ]
  }]
}
EOF
api_ok "Anlegen der Rolle $AGENT_ROLE"

log "Lege User $AGENT_USER an ($AGENT_ROLE)"
api PUT "/_plugins/_security/api/internalusers/$AGENT_USER" <<EOF
{"password": "$(json_escape "$AGENT_PW")", "backend_roles": [],
 "description": "Agent, liest und schreibt Vektor-Daten (vector/users.sh)"}
EOF
api_ok "Anlegen von $AGENT_USER"

api PUT "/_plugins/_security/api/rolesmapping/$AGENT_ROLE" <<EOF
{"users": ["$(json_escape "$AGENT_USER")"], "description": "vector/users.sh"}
EOF
api_ok "Rollen-Mapping $AGENT_ROLE"

# Nur lesen: Suchen (inkl. k-NN/hybrid über die Default-Search-Pipeline des
# Index), Dokumente holen, Mapping ansehen. Schreiben darf der Search-User nicht,
# denn Handbuchtexte landen ungefiltert im Prompt der Agents.
# cluster:monitor/main, nodes/info und state braucht Dashboards, wenn der User als
# Data-Source-Account dient (Version und Plugins abfragen, siehe workspace.md).
log "Lege Rolle $SEARCH_ROLE an (Index-Muster: $SEARCH_INDEX_PATTERNS)"
api PUT "/_plugins/_security/api/roles/$SEARCH_ROLE" <<EOF
{
  "description": "Vektor-Daten nur lesen und suchen (vector/users.sh)",
  "cluster_permissions": [
    "cluster_composite_ops_ro",
    "indices:data/read/scroll*",
    "cluster:monitor/main",
    "cluster:monitor/nodes/info",
    "cluster:monitor/state"
  ],
  "index_permissions": [{
    "index_patterns": $(json_list "$SEARCH_INDEX_PATTERNS"),
    "allowed_actions": [
      "read",
      "indices:admin/mappings/get",
      "indices:admin/resolve/index",
      "indices:admin/aliases/get"
    ]
  }]
}
EOF
api_ok "Anlegen der Rolle $SEARCH_ROLE"

log "Lege User $SEARCH_USER an ($SEARCH_ROLE)"
api PUT "/_plugins/_security/api/internalusers/$SEARCH_USER" <<EOF
{"password": "$(json_escape "$SEARCH_PW")", "backend_roles": [],
 "description": "Such-Agent, liest Vektor-Daten (vector/users.sh)"}
EOF
api_ok "Anlegen von $SEARCH_USER"

api PUT "/_plugins/_security/api/rolesmapping/$SEARCH_ROLE" <<EOF
{"users": ["$(json_escape "$SEARCH_USER")"], "description": "vector/users.sh"}
EOF
api_ok "Rollen-Mapping $SEARCH_ROLE"

# --- 6. Prüfen ----------------------------------------------------------------

# Zugangsdaten per stdin an curl, damit sie nicht in der Prozessliste stehen.
check_login() {
    local user="$1" pw="$2" code
    code="$(printf 'user = "%s:%s"\n' "$(json_escape "$user")" "$(json_escape "$pw")" \
        | curl -s -o "$RESP_FILE" -w '%{http_code}' -K - --cacert "$CERT_DIR/root-ca.pem" \
            "$URL/_plugins/_security/authinfo")" || code=000
    [ "$code" = "200" ] || die "Login als $user fehlgeschlagen (HTTP $code): $(cat "$RESP_FILE")"
    log "Login ok: $user, Rollen $(grep -o '"roles":\[[^]]*\]' "$RESP_FILE" | cut -d: -f2)"
}
check_login "$ADMIN_USER" "$ADMIN_PW"
check_login "$AGENT_USER" "$AGENT_PW"
check_login "$SEARCH_USER" "$SEARCH_PW"

cat <<EOF

Fertig: Auth und TLS aktiv auf $URL
  Admin:          $ADMIN_USER (all_access)
  Agent:          $AGENT_USER (Rolle $AGENT_ROLE auf $AGENT_INDEX_PATTERNS, schreiben)
  Search:         $SEARCH_USER (Rolle $SEARCH_ROLE auf $SEARCH_INDEX_PATTERNS, nur lesen)
  Passwörter:     $USERS_ENV
  CA-Zertifikat:  $CERT_DIR/root-ca.pem (für Clients)
  Admin-Zert.:    $CERT_DIR/admin.pem + admin-key.pem (Notzugang, nutzen bin/*.sh)

Test:  source $USERS_ENV
       curl --cacert "\$OPENSEARCH_CACERT" -u "\$OPENSEARCH_AGENT_USER:\$OPENSEARCH_AGENT_PASSWORD" $URL/_cat/indices

Index-Template und Search-Pipeline für Handbücher: vector/index.sh
EOF
