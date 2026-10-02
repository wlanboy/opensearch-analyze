# Anleitung: OpenSearch + k-NN auf einer RHEL-9-VM

Diese Anleitung führt Schritt für Schritt von einer frischen RHEL-9-VM bis zu einem
laufenden OpenSearch 3.9.0 mit k-NN-Plugin (faiss, lucene). Grundlage ist
[install.sh](install.sh). Hintergründe und alle Optionen stehen in
[README.md](README.md).

Ergebnis:

- OpenSearch läuft unter dem User `opensearch` aus `/opt/local/opensearch`
- HTTP auf Port 9200, zuerst nur lokal und optional später auch von außen
- Start und Stopp über `/opt/local/opensearch/bin/start.sh` und `stop.sh`, kein systemd

Befehle mit `sudo` brauchen einen User mit sudo-Rechten. Alle anderen Befehle laufen
als User `opensearch`, das ist in jedem Schritt angegeben.

---

## Schritt 0: Voraussetzungen prüfen

| Was | Mindestens |
|---|---|
| Betriebssystem | RHEL 9 (Rocky/Alma 9 funktionieren genauso) |
| Architektur | x86_64 (für aarch64 die arm64-Links aus der README nehmen) |
| RAM | 4 GB, für echte Vektor-Daten deutlich mehr |
| Plattenplatz | 2 GB für die Installation, dazu Platz für die Daten |
| Zugang | SSH mit sudo-Rechten |

```bash
cat /etc/redhat-release   # Red Hat Enterprise Linux release 9.x
uname -m                  # x86_64
free -g                   # RAM
df -h /opt                # freier Platz
```

## Schritt 1: Pakete installieren

Meist sind die Pakete schon installiert. Der Befehl schadet aber nicht:

```bash
sudo dnf install -y curl tar coreutils diffutils procps-ng findutils hostname
```

## Schritt 2: Dienst-User anlegen

OpenSearch darf nicht als root laufen. Wir legen dafür einen eigenen User an:

```bash
sudo useradd --create-home --comment "OpenSearch" opensearch
```

## Schritt 3: Systemlimits setzen

OpenSearch braucht mehr Memory-Maps und offene Dateien, als RHEL standardmäßig
erlaubt. Solange OpenSearch nur auf `127.0.0.1` lauscht, gibt es beim Start nur eine
Warnung. Bei Zugriff von außen (Schritt 10) bricht der Start aber ab. Deshalb die
Limits gleich setzen:

```bash
echo 'vm.max_map_count = 262144' | sudo tee /etc/sysctl.d/99-opensearch.conf
sudo sysctl --system | grep max_map_count

sudo tee /etc/security/limits.d/99-opensearch.conf <<'EOF'
opensearch  -  nofile   65535
opensearch  -  nproc    4096
opensearch  -  memlock  unlimited
EOF
```

## Schritt 4: Zielverzeichnis anlegen

```bash
sudo install -d -o opensearch -g opensearch /opt/local/opensearch
ls -ld /opt/local/opensearch   # Besitzer muss opensearch sein
```

## Schritt 5: Zum User `opensearch` wechseln

Alle weiteren Schritte laufen als `opensearch`:

```bash
sudo -iu opensearch
```

Wichtig ist das `-i`: Erst damit gelten die Limits aus Schritt 3. Kurz prüfen:

```bash
whoami       # opensearch
ulimit -Hn   # 65535
ulimit -l    # unlimited
```

## Schritt 6: Dateien herunterladen

Hat die VM Internetzugang, direkt ins Home-Verzeichnis laden. Alternativ macht
das [download.sh](download.sh) (siehe README, Abschnitt 1), es muss dafür wie
`install.sh` in Schritt 7 auf die VM kopiert werden.


```bash
cd ~
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz
curl -fLO https://artifacts.opensearch.org/releases/core/opensearch/3.9.0/opensearch-min-3.9.0-linux-x64.tar.gz.sha512
curl -fLO https://ci.opensearch.org/ci/dbc/distribution-build-opensearch/3.9.0/12228/linux/x64/tar/builds/opensearch/plugins/opensearch-knn-3.9.0.0.zip
```

**Ohne Internetzugang:** Dieselben drei Dateien auf dem eigenen Rechner laden und
kopieren. Das geht entweder direkt zum User `opensearch`, falls dieser per SSH
erreichbar ist, oder über den eigenen User:

```bash
# auf dem eigenen Rechner
scp opensearch-min-3.9.0-linux-x64.tar.gz* opensearch-knn-3.9.0.0.zip <ich>@<vm>:/tmp/

# auf der VM (als <ich>)
sudo mv /tmp/opensearch-min-3.9.0-linux-x64.tar.gz* /tmp/opensearch-knn-3.9.0.0.zip ~opensearch/
sudo chown opensearch:opensearch ~opensearch/opensearch-*
```

Prüfen (als `opensearch`):

```bash
ls -lh ~
# opensearch-knn-3.9.0.0.zip                    ~59M
# opensearch-min-3.9.0-linux-x64.tar.gz         ~241M
# opensearch-min-3.9.0-linux-x64.tar.gz.sha512
```

## Schritt 7: Installationsskript auf die VM bringen

Benötigt werden nur `install.sh` und optional `install.env`. Vom eigenen Rechner aus
dem Repo:

```bash
# auf dem eigenen Rechner
scp vector/install.sh vector/install.env.example <ich>@<vm>:/tmp/

# auf der VM (als <ich>)
sudo install -o opensearch -g opensearch -m 0755 /tmp/install.sh ~opensearch/install.sh
sudo install -o opensearch -g opensearch -m 0644 /tmp/install.env.example ~opensearch/install.env.example
```

Danach als `opensearch` die Konfiguration anlegen:

```bash
cd ~
cp install.env.example install.env
vi install.env
```

Für den Anfang reichen zwei Zeilen. Alles andere hat sinnvolle Defaults:

```bash
# Heap: Hälfte des RAMs ist Default; auf einer 8-GB-VM z.B.:
HEAP_SIZE=2g
# Prüfsumme des k-NN-Zips (3.9.0, x64), siehe README:
KNN_SHA512=5c2edeb3b8b287136393362012ac8067f31660e3b0806a4b77909f4b0f728e1ba271c31c9c7d1cdf1fc1dd23d2865e050e5d54919b56f8b0656318212364c44f
```

## Schritt 8: Installieren und starten

Als `opensearch`:

```bash
~/install.sh --start
```

Die Ausgabe sollte ungefähr so aussehen:

```
==> Gefundene Version: 3.9.0
==> Prüfsumme ok: /home/opensearch/opensearch-min-3.9.0-linux-x64.tar.gz
==> Prüfsumme ok: /home/opensearch/opensearch-knn-3.9.0.0.zip
==> Entpacke ... nach /opt/local/opensearch/opensearch-3.9.0
==> Installiere k-NN-Plugin aus /home/opensearch/opensearch-knn-3.9.0.0.zip
...
Warte auf http://127.0.0.1:9200 ...
OpenSearch läuft (PID 12345)

Fertig: OpenSearch 3.9.0 + k-NN 3.9.0.0 (faiss, lucene) unter /opt/local/opensearch
```

Wichtig ist der Text in Klammern: `(faiss, lucene)`. Steht dort
`(nur lucene)`, war es das falsche k-NN-Zip, siehe Fehlerbehebung.

## Schritt 9: Funktion prüfen

Status und Plugins:

```bash
/opt/local/opensearch/bin/status.sh
```

`"status" : "green"` und eine Zeile mit `opensearch-knn 3.9.0.0` müssen erscheinen.

Ein Test-Index mit faiss-Vektoren, drei Dokumente und eine Suche:

```bash
curl -s -XPUT localhost:9200/knn-test -H 'Content-Type: application/json' -d '{
  "settings": { "index.knn": true },
  "mappings": { "properties": { "v": {
    "type": "knn_vector", "dimension": 3,
    "method": { "name": "hnsw", "engine": "faiss", "space_type": "l2" }
  } } }
}'

curl -s -XPOST 'localhost:9200/knn-test/_doc?refresh=true' -H 'Content-Type: application/json' -d '{"v":[1,2,3]}'
curl -s -XPOST 'localhost:9200/knn-test/_doc?refresh=true' -H 'Content-Type: application/json' -d '{"v":[4,5,6]}'
curl -s -XPOST 'localhost:9200/knn-test/_doc?refresh=true' -H 'Content-Type: application/json' -d '{"v":[1,2,2]}'

curl -s 'localhost:9200/knn-test/_search?pretty' -H 'Content-Type: application/json' -d '{
  "size": 2,
  "query": { "knn": { "v": { "vector": [1,2,3], "k": 2 } } }
}'
```

Erwartet werden die Treffer `[1,2,3]` (Score 1.0) und `[1,2,2]` (Score 0.5).
Danach prüfen, ob faiss geladen ist, und den Test-Index löschen:

```bash
curl -s localhost:9200/_plugins/_knn/stats | grep -o '"faiss_initialized":[a-z]*'   # true
curl -s -XDELETE localhost:9200/knn-test
```

Die Installation ist damit fertig. Die nächsten Schritte sind optional.

## Schritt 10 (optional): Zugriff von anderen Rechnern

> **Achtung:** Ohne Schritt 12 hat diese Installation **keine Authentifizierung und
> kein TLS**. Jeder, der Port 9200 erreicht, kann alle Daten lesen und löschen.
> Entweder vorher Schritt 12 ausführen oder nur in einem abgeschotteten Netz öffnen
> und den Zugriff per Firewall auf bekannte Clients beschränken.

In `~/install.env` (als `opensearch`) die IP der VM eintragen:

```bash
NETWORK_HOST=10.0.0.11
```

Übernehmen und neu starten:

```bash
~/install.sh --restart
```

Die Firewall öffnen (als User mit sudo), am besten nur für das Client-Netz:

```bash
sudo firewall-cmd --permanent --add-rich-rule='rule family="ipv4" source address="10.0.0.0/24" port port="9200" protocol="tcp" accept'
sudo firewall-cmd --reload
```

Vom Client aus testen: `curl http://10.0.0.11:9200` (nach Schritt 12:
`curl -k -u agent:<passwort> https://10.0.0.11:9200`)

## Schritt 11 (optional): Automatisch beim Booten starten

Ohne systemd übernimmt das cron. Als `opensearch` ausführen:

```bash
crontab -e
```

und diese Zeile eintragen:

```
@reboot /opt/local/opensearch/bin/start.sh >/dev/null 2>&1
```

Zum Testen die VM neu starten und danach `/opt/local/opensearch/bin/status.sh`
aufrufen.

## Schritt 12 (optional): Auth und TLS einschalten

Danach spricht OpenSearch nur noch HTTPS und verlangt einen Login. Es gibt zwei User:

- `admin` darf alles.
- `agent` darf Vektor-Indizes anlegen und Dokumente anlegen, ändern, löschen und
  suchen. Indizes löschen darf er nicht.

Das Security-Plugin ins Home-Verzeichnis laden (als `opensearch`, ohne Internet wie
in Schritt 6 per `scp`):

```bash
cd ~
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/3.9.0.0/opensearch-security-3.9.0.0.zip
curl -fLO https://repo1.maven.org/maven2/org/opensearch/plugin/opensearch-security/3.9.0.0/opensearch-security-3.9.0.0.zip.sha512
```

`users.sh` wie in Schritt 7 neben `install.sh` nach `~opensearch/` kopieren
(`sudo install -o opensearch -g opensearch -m 0755 /tmp/users.sh ~opensearch/users.sh`),
dann als `opensearch`:

```bash
~/users.sh
```

Das Skript erzeugt Zertifikate, installiert das Plugin, startet OpenSearch neu und
legt die User an. Am Ende steht:

```
==> Login ok: admin, Rollen ["all_access","security_rest_api_access"]
==> Login ok: agent, Rollen ["vector_agent"]

Fertig: Auth und TLS aktiv auf https://127.0.0.1:9200
```

Die Passwörter stehen in `/opt/local/opensearch/config/users.env`. Test:

```bash
source /opt/local/opensearch/config/users.env
curl --cacert "$OPENSEARCH_CACERT" -u "$OPENSEARCH_AGENT_USER:$OPENSEARCH_AGENT_PASSWORD" "$OPENSEARCH_URL/_cat/indices?v"
```

Die Test-Befehle aus Schritt 9 brauchen jetzt `https://`, `--cacert` und `-u`.
`status.sh` funktioniert ohne Passwort, denn es nutzt das Admin-Zertifikat. Neue
Passwörter: `~/users.sh --rotate`. Details stehen in der README unter „Auth und User“.

---

## Alltag

Als `opensearch` (`sudo -iu opensearch`):

| Aufgabe | Befehl |
|---|---|
| Starten | `/opt/local/opensearch/bin/start.sh` |
| Stoppen | `/opt/local/opensearch/bin/stop.sh` |
| Status | `/opt/local/opensearch/bin/status.sh` |
| Log ansehen | `tail -f /opt/local/opensearch/logs/opensearch-vector.log` |
| Einstellung ändern | `vi ~/install.env`, dann `~/install.sh --restart` |
| Weitere OpenSearch-Settings | `vi /opt/local/opensearch/config/opensearch.local.yml`, dann `~/install.sh --restart` |
| Upgrade | neue Tarball- und Zip-Dateien nach `~` legen, dann `~/install.sh --restart` |

Dateien in `/opt/local/opensearch/config` nicht direkt bearbeiten (außer
`opensearch.local.yml`): Das Skript überschreibt sie beim nächsten Lauf.

## Fehlerbehebung

Zuerst immer in die Logs schauen:

```bash
tail -50 /opt/local/opensearch/logs/startup.log
tail -100 /opt/local/opensearch/logs/opensearch-vector.log
```

| Meldung | Ursache und Lösung |
|---|---|
| `FEHLER: Nicht als root ausführen` | Mit `sudo -iu opensearch` wechseln und dort ausführen |
| `... existiert nicht oder ist nicht schreibbar` | Schritt 4 fehlt oder der Besitzer stimmt nicht: `sudo chown opensearch:opensearch /opt/local/opensearch` |
| `opensearch-knn-3.9.0.0.zip nicht in /home/opensearch gefunden` | Datei fehlt oder liegt tiefer als `~/<ordner>/`. Schritt 6 wiederholen |
| `SHA-512 stimmt nicht` | Download unvollständig oder kaputt: Datei löschen und neu laden |
| `Fertig: ... (nur lucene)` | Das Maven-Zip statt des ci.opensearch.org-Zips wurde verwendet. Richtiges Zip laden (Schritt 6), altes ersetzen und `~/install.sh --restart` ausführen |
| `max virtual memory areas vm.max_map_count [65530] is too low` | Schritt 3 (sysctl) fehlt |
| `max file descriptors [4096] ... is too low` | Schritt 3 (limits) fehlt oder es wurde ohne `-i` gewechselt. Neu mit `sudo -iu opensearch` anmelden |
| `memory locking requested ... but memory is not locked` | `MEMORY_LOCK=true` gesetzt, aber `ulimit -l` ist nicht `unlimited`. Schritt 3 prüfen oder `MEMORY_LOCK=false` setzen |
| `BindException: Address already in use` | Port 9200/9300 ist schon belegt (`ss -ltnp \| grep 9200`). Anderen Prozess beenden oder `HTTP_PORT`/`TRANSPORT_PORT` ändern |
| `OutOfMemoryError` / Prozess verschwindet | `HEAP_SIZE` zu groß für die VM (OOM-Killer: `sudo dmesg \| grep -i oom`). Heap verkleinern |
| `curl: (7) Failed to connect` von außen | `NETWORK_HOST` nicht gesetzt (Schritt 10) oder Firewall zu |
| `opensearch-security-3.9.0.0.zip nicht in ... gefunden` | Security-Zip fehlt (Schritt 12) |
| `curl: (52) Empty reply from server` | Nach Schritt 12 läuft nur noch HTTPS: `https://` verwenden |
| `curl: (60) SSL certificate problem` | `--cacert /opt/local/opensearch/config/certs/root-ca.pem` angeben (oder `-k`) |
| `Unauthorized` / HTTP 401 | Falsches Passwort, siehe `config/users.env` |
| HTTP 403 `no permissions for [indices:admin/delete]` | `agent` darf keine Indizes löschen, als `admin` ausführen |
| `Es gab Änderungen, OpenSearch läuft noch mit dem alten Stand` | Skript mit `--restart` aufrufen |

## Deinstallation

```bash
sudo -iu opensearch /opt/local/opensearch/bin/stop.sh
sudo -iu opensearch crontab -r            # nur falls Schritt 11 gemacht wurde
sudo rm -rf /opt/local/opensearch         # löscht auch alle Daten!
sudo rm -f /etc/sysctl.d/99-opensearch.conf /etc/security/limits.d/99-opensearch.conf
sudo userdel -r opensearch
```
