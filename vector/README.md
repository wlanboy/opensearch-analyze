# OpenSearch + k-NN auf RHEL installieren

[install.sh](install.sh) installiert OpenSearch (min-Distribution mit gebündeltem
JDK) und das k-NN-Plugin auf RHEL 8/9 (oder Rocky/Alma). Das Skript lädt selbst
nichts herunter. Es sucht die vorher heruntergeladenen Dateien im Home-Verzeichnis
des aktuellen Users, entpackt sie nach `/opt/local/opensearch` und schreibt dort die
Konfiguration. Es legt keine systemd-Unit an, braucht kein root und schreibt nichts
außerhalb von `/opt/local/opensearch`. Das Skript lässt sich beliebig oft ausführen.

Schritt-für-Schritt-Anleitung für eine frische RHEL-9-VM: [manual.md](manual.md).

## 1. Dateien herunterladen

Für OpenSearch **2.19.5** (identisch mit der Version in
[docker-compose.yml](../docker-compose.yml)):

| Datei | x86_64 | aarch64 |
|---|---|---|
| OpenSearch (min, mit JDK) | [opensearch-min-2.19.5-linux-x64.tar.gz](https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-x64.tar.gz) | [opensearch-min-2.19.5-linux-arm64.tar.gz](https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-arm64.tar.gz) |
| Prüfsumme dazu | [.sha512](https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-x64.tar.gz.sha512) | [.sha512](https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-arm64.tar.gz.sha512) |
| k-NN-Plugin mit faiss/nmslib | [opensearch-knn-2.19.5.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/2.19.5/11769/linux/x64/tar/builds/opensearch/plugins/opensearch-knn-2.19.5.0.zip) | [opensearch-knn-2.19.5.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/2.19.5/11769/linux/arm64/tar/builds/opensearch/plugins/opensearch-knn-2.19.5.0.zip) |

Als Befehle (x86_64):

```bash
cd ~
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-x64.tar.gz
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/2.19.5/opensearch-min-2.19.5-linux-x64.tar.gz.sha512
curl -fLO https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/2.19.5/11769/linux/x64/tar/builds/opensearch/plugins/opensearch-knn-2.19.5.0.zip
```

Hat der Server keinen Internetzugang, die Dateien woanders laden und per `scp` ins
Home-Verzeichnis kopieren. Ein Unterordner wie `~/Downloads` geht auch, denn das
Skript sucht eine Ebene tief. Die `.sha512`-Datei ist optional. Liegt sie neben dem
Tarball, prüft das Skript die Datei damit.

**Warum das k-NN-Plugin von ci.opensearch.org?** Das gleichnamige Zip auf Maven
Central
([opensearch-knn-2.19.5.0.zip](https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-knn/2.19.5.0/opensearch-knn-2.19.5.0.zip))
enthält nur die JARs. Die nativen Libraries für **faiss** und **nmslib** fehlen,
damit funktioniert nur `engine: lucene`. Das Zip aus dem Distribution-Build enthält
zusätzlich `lib/libopensearchknn_faiss*.so` usw. Aus diesem Build (ID 11769) besteht
auch das offizielle Voll-Bundle bzw. das Docker-Image. Installiert ihr trotzdem die
Maven-Variante, warnt das Skript.

Für das CI-Zip gibt es keine `.sha512`. Die SHA-512 des Zips für 2.19.5 x64 lautet:

```
733d6389da08338bb997ebefe660e3fc757e3f8851543c2dd0335481015ae4169d267d9e80834074876076f5161470e9939ef8d5d093323d60f84550c2b0facb
```

Tragt sie als `KNN_SHA512` in `install.env` ein, dann prüft das Skript sie.

**Andere Version:** In den URLs `2.19.5` ersetzen. Die Build-ID für den k-NN-Link
steht unter
`https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/<V>/latest/linux/x64/tar/builds/opensearch/manifest.yml`
bei `build.id`. Statt der ID funktioniert auch `latest`.

## 2. Zielverzeichnis anlegen (einmalig, als root)

OpenSearch startet nicht als root. Das Skript läuft deshalb unter dem User, der
später auch OpenSearch betreibt, und dem muss `/opt/local/opensearch` gehören:

```bash
sudo install -d -o "$USER" -g "$(id -gn)" /opt/local/opensearch
```

## 3. Installieren

```bash
cp vector/install.env.example vector/install.env   # optional, Defaults passen für single-node
vector/install.sh --start
```

Ohne `--start` wird nur installiert und konfiguriert, aber nichts gestartet.
`vector/install.sh` muss nicht auf dem Server im Repo liegen: Es reicht, `install.sh`
(und ggf. `install.env` daneben) auf den Server zu kopieren.

Das Skript führt diese Schritte aus, jeder davon wird übersprungen, wenn er schon
erledigt ist:

1. sucht `opensearch-min-<V>-linux-<arch>.tar.gz` und `opensearch-knn-<V>.0.zip` in
   `~` (ohne `OPENSEARCH_VERSION` nimmt es die höchste gefundene Version) und prüft
   die SHA-512
2. entpackt nach `/opt/local/opensearch/opensearch-<V>`
3. installiert das k-NN-Plugin mit `opensearch-plugin install`
4. schreibt die Konfiguration nach `/opt/local/opensearch/config` (Backup bei Änderung)
5. setzt den Symlink `/opt/local/opensearch/current` auf die Version
6. legt `bin/start.sh`, `bin/stop.sh` und `bin/status.sh` an

## Starten, Stoppen, Status

```bash
/opt/local/opensearch/bin/start.sh     # startet im Hintergrund, wartet bis HTTP antwortet
/opt/local/opensearch/bin/stop.sh
/opt/local/opensearch/bin/status.sh    # Prozess, Cluster-Health, Plugins

tail -f /opt/local/opensearch/logs/opensearch-vector.log   # <CLUSTER_NAME>.log
cat /opt/local/opensearch/logs/startup.log                 # Ausgabe beim Start

curl http://127.0.0.1:9200/_plugins/_knn/stats?pretty
```

Ohne systemd startet OpenSearch nach einem Reboot nicht von selbst. Wer das will,
trägt einen Cron-Eintrag beim User ein (`crontab -e`):

```
@reboot /opt/local/opensearch/bin/start.sh >/dev/null 2>&1
```

## Konfigurieren

| Was | Wo |
|---|---|
| Heap, Ports, Netzwerk, Cluster | Variablen in `vector/install.env`, danach `vector/install.sh --restart` |
| weitere `opensearch.yml`-Settings | `/opt/local/opensearch/config/opensearch.local.yml`, danach `vector/install.sh --restart` |
| zusätzliche JVM-Optionen | eigene Datei `config/jvm.options.d/<name>.options`, danach `bin/stop.sh && bin/start.sh` |
| Secrets | `OPENSEARCH_PATH_CONF=/opt/local/opensearch/config /opt/local/opensearch/current/bin/opensearch-keystore ...` |

`opensearch.yml`, `jvm.options`, `jvm.options.d/heap.options` und `bin/*.sh` erzeugt
das Skript. Wer sie von Hand ändert, verliert die Änderung beim nächsten Lauf (die
alte Version bleibt als `*.bak-<zeitstempel>` erhalten). `opensearch.local.yml` hängt
das Skript unverändert an `opensearch.yml` an, zum Beispiel:

```yaml
knn.algo_param.index_thread_qty: 4
knn.memory.circuit_breaker.limit: 60%
```

Mit `--restart` startet das Skript OpenSearch nur dann neu, wenn es läuft und sich
tatsächlich etwas geändert hat. Ohne `--restart` erscheint bei Änderungen nur ein
Hinweis.

### Heap und Speicher

`HEAP_SIZE=auto` setzt den Heap auf 50 % des RAMs (höchstens 31g). Die faiss- und
nmslib-Graphen liegen **außerhalb** des Heaps im nativen Speicher. Der k-NN
Circuit Breaker (`knn.memory.circuit_breaker.limit`, Default 50 %) bezieht sich auf
den Speicher, der neben dem Heap übrig bleibt. Bei großen Vektor-Indizes lohnt sich
deshalb eher ein kleinerer Heap, z.B. `HEAP_SIZE=8g` auf einer 64-GB-Maschine.

### Netzwerk, Sicherheit und Systemlimits

Die min-Distribution enthält **kein Security-Plugin**: kein TLS, keine
Authentifizierung. Deshalb gilt der Default `NETWORK_HOST=127.0.0.1`. Auth und TLS
schaltet [users.sh](users.sh) ein, siehe [Auth und User](#auth-und-user). Ohne
users.sh den Zugang von anderen Hosts per Firewall bzw. Netzsegment absichern.

Sobald `network.host` keine Loopback-Adresse mehr ist, prüft OpenSearch beim Start
Systemlimits und bricht ab, wenn sie nicht passen. Das Skript ändert außerhalb von
`/opt/local/opensearch` nichts und warnt nur. Einmalig als root setzen:

```bash
# vm.max_map_count (Pflicht)
echo 'vm.max_map_count = 262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system

# offene Dateien (Pflicht) und Memory-Lock (nur für MEMORY_LOCK=true)
sudo tee /etc/security/limits.d/99-opensearch.conf <<EOF
$USER  -  nofile   65535
$USER  -  memlock  unlimited
EOF
# danach neu einloggen; prüfen mit: ulimit -Hn; ulimit -l
```

Bei Bedarf noch die Ports freigeben:
`sudo firewall-cmd --permanent --add-port={9200,9300}/tcp && sudo firewall-cmd --reload`

### Mehrknoten-Cluster

Auf jedem Knoten mit demselben `CLUSTER_NAME`:

```bash
NETWORK_HOST=10.0.0.11              # eigene IP
SEED_HOSTS=10.0.0.11,10.0.0.12,10.0.0.13
INITIAL_CLUSTER_MANAGER_NODES=os-vec-1,os-vec-2,os-vec-3   # node.name der Knoten
```

`INITIAL_CLUSTER_MANAGER_NODES` wird nur beim allerersten Start des Clusters
gebraucht. Danach sollte die Variable leer sein, siehe
[Discovery-Doku](https://docs.opensearch.org/latest/tuning-your-cluster/).

## Auth und User

[users.sh](users.sh) installiert das Security-Plugin, schaltet TLS (HTTP und
Transport) und Basic-Auth ein und legt zwei User an:

| User | Rechte |
|---|---|
| `admin` | alles (`all_access` und Security-REST-API) |
| `agent` | Rolle `vector_agent` auf `AGENT_INDEX_PATTERNS` (Default `*`): Indizes und Mappings anlegen, Dokumente/Vektoren anlegen, ändern, löschen (auch `_delete_by_query`), suchen inkl. k-NN, `_bulk`, `_msearch`. Indizes löschen und Settings ändern darf `agent` **nicht** |

Zusätzlich zu den Dateien aus Abschnitt 1 wird das Security-Plugin gebraucht. Es
ist reines Java, das Zip von Maven Central passt deshalb:

```bash
cd ~
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/2.19.5.0/opensearch-security-2.19.5.0.zip
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/2.19.5.0/opensearch-security-2.19.5.0.zip.sha512
```

Dann (nach `install.sh`, `users.sh` muss neben `install.sh` liegen):

```bash
vector/users.sh
```

Das Skript

1. erzeugt unter `config/certs/` eine eigene CA, ein Node-Zertifikat (SANs:
   `localhost`, `127.0.0.1`, Hostname, `NETWORK_HOST`, `EXTRA_SANS`) und ein
   Admin-Zertifikat,
2. schreibt `config/opensearch.security.yml` (hängt `install.sh` an
   `opensearch.yml` an) und die Startkonfiguration in `config/opensearch-security/`
   (ohne Demo-User),
3. ruft `install.sh --start` auf. Das installiert das Plugin und startet neu.
4. Danach legt es User, Rolle und Mapping per Security-REST-API an und prüft beide
   Logins.

Die Passwörter (32 Zeichen, generiert) stehen in `config/users.env` (Modus 0600):

```bash
source /opt/local/opensearch/config/users.env
curl --cacert "$OPENSEARCH_CACERT" -u "$OPENSEARCH_AGENT_USER:$OPENSEARCH_AGENT_PASSWORD" \
  "$OPENSEARCH_URL/_cat/indices?v"
```

Clients auf anderen Rechnern brauchen `config/certs/root-ca.pem` als CA oder müssen
die Zertifikatsprüfung abschalten (`curl -k`).

- **Erneut ausführen:** Das ist idempotent. Die Passwörter aus `users.env` werden
  neu gesetzt, andere User (z.B. von `adduser.sh`) bleiben erhalten.
- **Passwörter wechseln:** `vector/users.sh --rotate` oder fest vorgeben:
  `ADMIN_PASSWORD=... AGENT_PASSWORD=... vector/users.sh`
- **Andere Namen/Indizes:** `ADMIN_USER`, `AGENT_USER`, `AGENT_ROLE` und
  `AGENT_INDEX_PATTERNS` in `install.env`, z.B. `AGENT_INDEX_PATTERNS=vectors-*,rag-*`
- **Notzugang:** `bin/start.sh` und `bin/status.sh` authentifizieren sich mit dem
  Admin-Zertifikat (`config/certs/admin.pem`). Damit lässt sich die Security-API auch
  ohne Passwort nutzen:
  `curl --cacert certs/root-ca.pem --cert certs/admin.pem --key certs/admin-key.pem https://127.0.0.1:9200/_plugins/_security/api/internalusers`
- **Zertifikate erneuern:** `config/certs/node.pem` bzw. `admin.pem` löschen und
  `users.sh` ausführen. Ein neues Node-Zertifikat gibt es automatisch, wenn sich
  Hostname, `NETWORK_HOST` oder `EXTRA_SANS` ändern.
- **Upgrade:** Das Security-Zip der neuen Version gehört mit nach `~`. `install.sh`
  installiert das Plugin wieder, solange `config/opensearch.security.yml` existiert.
- **Auth wieder ausschalten:** `config/opensearch.security.yml` löschen und
  `install.sh --restart` ausführen. Das Plugin wird dann entfernt.

**Mehrknoten-Cluster:** Alle Knoten müssen dieselbe CA nutzen und gleichzeitig auf
TLS umgestellt werden. Ein Knoten mit TLS und einer ohne TLS finden sich nicht.
`users.sh` zuerst auf einem Knoten ausführen. Auf den übrigen dann vorher
`config/certs/root-ca.pem`, `config/certs/root-ca-key.pem` und `config/users.env`
vom ersten Knoten kopieren und `users.sh` ausführen.

## Upgrade

Neue Dateien (Tarball + k-NN-Zip der neuen Version) nach `~` legen und
`vector/install.sh --restart` ausführen. Ohne `OPENSEARCH_VERSION` nimmt das Skript
automatisch die höchste gefundene Version. Die neue Version landet parallel in
`/opt/local/opensearch/opensearch-<neu>`, und `current` zeigt danach dorthin. Die
Daten in `data/` bleiben unangetastet. Rollback: `OPENSEARCH_VERSION=<alt>` setzen
und das Skript erneut ausführen. Das funktioniert nur, solange die neue Version die
Daten noch nicht migriert hat. Alte Versionsverzeichnisse räumt man von Hand auf.

## Verzeichnisse

| Pfad unter `/opt/local/opensearch` | Inhalt |
|---|---|
| `opensearch-<V>/` | Binaries, JDK, Plugins (read-only) |
| `current` | Symlink auf aktive Version |
| `config/` | `opensearch.yml`, `opensearch.local.yml`, `jvm.options(.d)`, Keystore |
| `config/certs/`, `config/opensearch-security/`, `config/users.env` | nur mit `users.sh`: Zertifikate, Security-Startkonfiguration, Passwörter |
| `data/` | Index-Daten |
| `logs/` | OpenSearch-Log, GC-Log, `startup.log` |
| `bin/` | `start.sh`, `stop.sh`, `status.sh`, `env.sh` |
| `run/` | PID-Datei |
| `tmp/` | JVM-Temp-Verzeichnis |
