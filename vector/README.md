# OpenSearch + k-NN auf RHEL installieren

[install.sh](install.sh) installiert OpenSearch (min-Distribution mit gebündeltem
JDK) und das k-NN-Plugin auf RHEL 8/9 (oder Rocky/Alma). Das Skript lädt selbst
nichts herunter. Es sucht die vorher heruntergeladenen Dateien im Home-Verzeichnis
des aktuellen Users, entpackt sie nach `/opt/local/opensearch` und schreibt dort die
Konfiguration. Es legt keine systemd-Unit an, braucht kein root und schreibt nichts
außerhalb von `/opt/local/opensearch`. Das Skript lässt sich beliebig oft ausführen.

Schritt-für-Schritt-Anleitung für eine frische RHEL-9-VM: [manual.md](manual.md).

## 1. Dateien herunterladen

Für OpenSearch **3.9.0** (identisch mit der Version in
[docker-compose.yml](../docker-compose.yml)):

| Datei | x86_64 | aarch64 |
|---|---|---|
| OpenSearch (min, mit JDK) | [opensearch-min-3.9.0-linux-x64.tar.gz](https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz) | [opensearch-min-3.9.0-linux-arm64.tar.gz](https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-arm64.tar.gz) |
| Prüfsumme dazu | [.sha512](https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz.sha512) | [.sha512](https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-arm64.tar.gz.sha512) |
| k-NN-Plugin mit faiss | [opensearch-knn-3.9.0.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar/builds/opensearch/plugins/opensearch-knn-3.9.0.0.zip) | [opensearch-knn-3.9.0.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/arm64/tar/builds/opensearch/plugins/opensearch-knn-3.9.0.0.zip) |
| neural-search (hybride Suche, optional) | [opensearch-neural-search-3.9.0.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar/builds/opensearch/plugins/opensearch-neural-search-3.9.0.0.zip) | [opensearch-neural-search-3.9.0.0.zip](https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/arm64/tar/builds/opensearch/plugins/opensearch-neural-search-3.9.0.0.zip) |

Am einfachsten lädt [download.sh](download.sh) alles nach `ARTIFACT_DIR` (Default
`~`). Es liest dieselbe `install.env`, ermittelt die Build-ID selbst, prüft
Tarball und Security-Zip gegen ihre `.sha512` und gibt am Ende `KNN_SHA512` und
`NEURAL_SHA512` für die `install.env` aus. Bereits geladene Dateien lädt es nicht
noch einmal:

```bash
vector/download.sh
```

Für einen Server ohne Internetzugang auf einem anderen Rechner `DOWNLOAD_ARCH`
(`x64`/`arm64`) und `ARTIFACT_DIR` in einer eigenen Datei setzen und
`download.sh --config <datei>` ausführen. Mit `NEURAL_SEARCH=false` lässt es
neural-search weg, mit `DOWNLOAD_SECURITY=true` lädt es auch das Security-Plugin
(siehe [Auth und User](#auth-und-user)).

Von Hand als Befehle (x86_64):

```bash
cd ~
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz.sha512
curl -fLO https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar/builds/opensearch/plugins/opensearch-knn-3.9.0.0.zip
curl -fLO https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar/builds/opensearch/plugins/opensearch-neural-search-3.9.0.0.zip
```

Hat der Server keinen Internetzugang, die Dateien woanders laden und per `scp` ins
Home-Verzeichnis kopieren. Ein Unterordner wie `~/Downloads` geht auch, denn das
Skript sucht eine Ebene tief. Die `.sha512`-Datei ist optional. Liegt sie neben dem
Tarball, prüft das Skript die Datei damit.

**Warum das k-NN-Plugin von ci.opensearch.org?** Das gleichnamige Zip auf Maven
Central
([opensearch-knn-3.9.0.0.zip](https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-knn/3.9.0.0/opensearch-knn-3.9.0.0.zip))
enthält nur die JARs. Die native Library für **faiss** fehlt, damit funktioniert
nur `engine: lucene`. Das Zip aus dem Distribution-Build enthält
zusätzlich `lib/libopensearchknn_faiss*.so` usw. Aus diesem Build (ID 12228) besteht
auch das offizielle Voll-Bundle bzw. das Docker-Image. Installiert ihr trotzdem die
Maven-Variante, warnt das Skript.

**nmslib** ist seit OpenSearch 3.0 veraltet. Die Library liegt zwar noch im Zip,
neue Indizes mit `engine: nmslib` lassen sich aber nicht mehr anlegen. Dieses
Setup nutzt faiss.

Für das CI-Zip gibt es keine `.sha512`. Die SHA-512 des Zips für 3.9.0 x64 lautet:

```
5c2edeb3b8b287136393362012ac8067f31660e3b0806a4b77909f4b0f728e1ba271c31c9c7d1cdf1fc1dd23d2865e050e5d54919b56f8b0656318212364c44f
```

Tragt sie als `KNN_SHA512` in `install.env` ein, dann prüft das Skript sie.

**neural-search** liefert die `hybrid`-Query und die Score-Kombination von BM25 und
k-NN. Ohne das Plugin funktioniert alles andere, die Agents müssen die beiden
Ergebnislisten dann aber selbst zusammenführen. Das Plugin hängt nur von k-NN ab,
ml-commons ist nicht nötig, weil die Embeddings außerhalb von OpenSearch entstehen
(siehe [pipeline.md](pipeline.md)). Auf Maven Central gibt es für 3.9.0 kein Zip,
deshalb der CI-Link. SHA-512 für 3.9.0 x64 (als `NEURAL_SHA512`):

```
6b177eac80b33cc30a9aab08f97b60052e84b79b15020461256609b9fcd9e4c70e13d4163499b2049a410921582d3ceb3c0331fa92f96a6cf26ead14155ed0a6
```

Liegt das Zip in `~`, installiert `install.sh` es automatisch (`NEURAL_SEARCH=auto`).
Mit `NEURAL_SEARCH=true` bricht das Skript ab, wenn es fehlt. `NEURAL_SEARCH=false`
entfernt das Plugin wieder.

**Andere Version:** In den URLs `3.9.0` ersetzen. Die Build-ID für den k-NN-Link
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
3. installiert das k-NN-Plugin (und ggf. neural-search) mit `opensearch-plugin install`
4. schreibt die Konfiguration nach `/opt/local/opensearch/config` (Backup bei Änderung)
5. setzt den Symlink `/opt/local/opensearch/current` auf die Version
6. legt `bin/start.sh`, `bin/stop.sh`, `bin/status.sh` und `bin/snapshot.sh` an

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

## Snapshots

Den Vektor-Index neu aufzubauen heißt, alle Handbücher neu zu embedden. Deshalb
setzt das Skript `path.repo` auf `SNAPSHOT_DIR` (Default
`/opt/local/opensearch/snapshots`) und legt `bin/snapshot.sh` an. Das Skript
registriert das fs-Repository `backup`, legt einen Snapshot aller Indizes an
(mit Security inklusive Security-Index) und behält die neuesten `SNAPSHOT_KEEP`
(Default 14). Täglich per Cron:

```
30 2 * * * /opt/local/opensearch/bin/snapshot.sh >>/opt/local/opensearch/logs/snapshot.log 2>&1
```

Einen Index zurückholen (`curl`-Optionen wie in `bin/status.sh`):

```bash
source /opt/local/opensearch/bin/env.sh
curl "${OPENSEARCH_CURL_OPTS[@]}" "$OPENSEARCH_URL/_cat/snapshots/backup?v"
curl "${OPENSEARCH_CURL_OPTS[@]}" -XDELETE "$OPENSEARCH_URL/manuals-de"
curl "${OPENSEARCH_CURL_OPTS[@]}" -XPOST -H 'Content-Type: application/json' \
  "$OPENSEARCH_URL/_snapshot/backup/<snapshot>/_restore?wait_for_completion=true" \
  -d '{"indices": "manuals-de"}'
```

`SNAPSHOT_DIR` liegt auf derselben Platte wie die Daten. Für echte Backups das
Verzeichnis zusätzlich wegsichern oder auf ein anderes Dateisystem legen. Im
Mehrknoten-Cluster muss `SNAPSHOT_DIR` ein gemeinsames Dateisystem (NFS) sein, das
auf allen Knoten unter demselben Pfad hängt.

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

`HEAP_SIZE=auto` setzt den Heap auf 50 % des RAMs (höchstens 31g). Die faiss-Graphen
liegen **außerhalb** des Heaps im nativen Speicher. Der k-NN
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
Transport) und Basic-Auth ein und legt drei User an:

| User | Rechte |
|---|---|
| `admin` | alles (`all_access` und Security-REST-API) |
| `agent` | Rolle `vector_agent` auf `AGENT_INDEX_PATTERNS` (Default `*`): Indizes und Mappings anlegen, Dokumente/Vektoren anlegen, ändern, löschen (auch `_delete_by_query`), suchen inkl. k-NN, `_bulk`, `_msearch`. Indizes löschen und Settings ändern darf `agent` **nicht**. Gedacht für die Ingest-Pipeline |
| `search` | Rolle `vector_search` auf `SEARCH_INDEX_PATTERNS` (Default wie `AGENT_INDEX_PATTERNS`): suchen inkl. k-NN/hybrid, `_msearch`, Dokumente holen, Mapping lesen. Schreiben und Löschen darf `search` **nicht**. Gedacht für Agents |

Agents bekommen nur den `search`-User. Handbuchtexte landen ungefiltert im Prompt,
und ein Agent mit Schreibrechten ließe sich per Prompt-Injection dazu bringen,
Dokumente zu ändern oder zu löschen.

Zusätzlich zu den Dateien aus Abschnitt 1 wird das Security-Plugin gebraucht. Es
ist reines Java, das Zip von Maven Central passt deshalb. `download.sh` lädt es mit
`DOWNLOAD_SECURITY=true` (Default `auto` lädt es erst, wenn `users.sh` schon
gelaufen ist), oder von Hand:

```bash
cd ~
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/3.9.0.0/opensearch-security-3.9.0.0.zip
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/3.9.0.0/opensearch-security-3.9.0.0.zip.sha512
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
  `ADMIN_PASSWORD=... AGENT_PASSWORD=... SEARCH_PASSWORD=... vector/users.sh`
- **Andere Namen/Indizes:** `ADMIN_USER`, `AGENT_USER`, `AGENT_ROLE`,
  `AGENT_INDEX_PATTERNS`, `SEARCH_USER`, `SEARCH_ROLE` und `SEARCH_INDEX_PATTERNS`
  in `install.env`, z.B. `SEARCH_INDEX_PATTERNS=manuals*` (deckt Indizes und Alias
  `manuals` ab)
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

## Index für Handbücher

[index.sh](index.sh) legt das Index-Template und die Search-Pipeline für
Handbuch-Chunks an. Pflicht ist die Dimension des Embedding-Modells:

```bash
echo 'EMBEDDING_DIM=1024' >>vector/install.env
vector/index.sh
```

| Objekt | Inhalt |
|---|---|
| Index-Template `manuals` | für `manuals-*`: `index.knn`, Feld `embedding` (faiss/hnsw, `EMBEDDING_SPACE`), Text-Felder mit `TEXT_ANALYZER` (Default `german`), `content.codes` für Fehlercodes und Parameternamen, Metadaten, `dynamic: strict`, Alias `manuals` |
| Search-Pipeline `manuals-hybrid` | RRF-Kombination von BM25 und k-NN, als `index.search.default_pipeline` gesetzt (nur mit neural-search) |

Das Template gilt nur für neue Indizes. Ändert sich `EMBEDDING_DIM` (anderes
Modell), warnt `index.sh` bei bestehenden Indizes: Die müssen neu angelegt und neu
befüllt werden. Felder, Ingest und Beispiel-Queries: [pipeline.md](pipeline.md).

## Upgrade

Neue Dateien (Tarball + k-NN-Zip der neuen Version, ggf. neural-search und Security) nach `~` legen und
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
| `snapshots/` | Snapshot-Repository `backup` (`SNAPSHOT_DIR`) |
| `logs/` | OpenSearch-Log, GC-Log, `startup.log` |
| `bin/` | `start.sh`, `stop.sh`, `status.sh`, `snapshot.sh`, `env.sh` |
| `run/` | PID-Datei |
| `tmp/` | JVM-Temp-Verzeichnis |
