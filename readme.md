# opensearch-analyze

Lokales Setup zum Betrieb und zur Analyse eines [OpenSearch](https://opensearch.org/)-Clusters:
ein Docker-Compose-Stack mit einem 2-Node-Cluster, Log-Shipping über
[Fluent Bit](https://fluentbit.io/) sowie ein Python-CLI-Tool, das Cluster-, Index- und
Node-Metriken auswertet und als Text- oder JSON-Report ausgibt.

## Bestandteile

| Komponente | Beschreibung |
|---|---|
| [docker-compose.yml](docker-compose.yml) | Startet den OpenSearch-Cluster (mit aktivem Security-Plugin), Fluent Bit und OpenSearch Dashboards |
| [opensearch-analyze.py](opensearch-analyze.py) | CLI-Report über Cluster-Health, Index-Stats und Node-Health |
| [conf/fluent-bit.conf](conf/fluent-bit.conf) | Fluent-Bit-Konfiguration (Syslog/Kernel-Log → OpenSearch) |
| [adduser.sh](adduser.sh) | Legt den read-only OpenSearch-User für opensearch-analyze.py an und schreibt ihn nach `.env` |
| [stress.sh](stress.sh) | Erzeugt Testdaten und teure Queries für lange-Queries-Analyse |

## Architektur

```
opensearch-1 ─┐
              ├─ Cluster "opensearch-cluster"
opensearch-2 ─┘
      │
      ├── fluent-bit ────────── liest /var/log/syslog, /var/log/kern.log
      └── opensearch-dashboards ── UI auf Port 5601
```

## Setup

```bash
cp .env-example .env
# OPENSEARCH_INITIAL_ADMIN_PASSWORD in .env auf ein starkes Passwort setzen
# (min. 8 Zeichen, Groß-/Kleinbuchstabe, Ziffer, Sonderzeichen)

docker compose up -d
./adduser.sh   # legt den read-only Analyzer-User an und schreibt ihn nach .env
```

`adduser.sh` legt eine minimal berechtigte Rolle (`cluster_monitor` +
`indices_monitor`) und einen zugehörigen User (`opensearch-analyzer`) über die
Security-REST-API an, generiert dafür ein zufälliges Passwort und schreibt
`OPENSEARCH_HOST`/`OPENSEARCH_USER`/`OPENSEARCH_PASSWORD`/`OPENSEARCH_INSECURE`
nach `.env`. Erneutes Ausführen rotiert das Passwort.

| Service | URL |
|---|---|
| OpenSearch Node 1 | https://localhost:9200 |
| OpenSearch Node 2 | https://localhost:9201 |
| OpenSearch Dashboards | http://localhost:5601 |
| Cluster-Health | https://localhost:9200/_cluster/health |

Das Cluster nutzt selbstsignierte Demo-Zertifikate (`OPENSEARCH_INSECURE=true`
in `.env`) — für produktive Cluster stattdessen `--ca-cert` mit einer echten CA
verwenden.

## opensearch-analyze.py

Analysiert Cluster-Health, Index-Stats (Search/Indexing/Store/Docs/Merge/Caches) und
Node-Health (Heap, Disk-Watermarks, Cache-Hit-Ratio, langsame Queries) über die OpenSearch-REST-API.
Liest Host/User/Passwort automatisch aus `.env`, sofern vorhanden — für den lokalen
Docker-Compose-Stack reicht nach `./adduser.sh` ein einfaches `./opensearch-analyze.py`.

```bash
./opensearch-analyze.py
./opensearch-analyze.py --json
./opensearch-analyze.py --watch --interval 15
./opensearch-analyze.py --long-queries-type cpu --long-queries-limit 5
```

| Option | Beschreibung |
|---|---|
| `--host` | OpenSearch-Basis-URL (Default: `.env`/`$OPENSEARCH_HOST` oder `http://localhost:9200`) |
| `--json` | Ausgabe als JSON statt Textreport |
| `--watch` | Wiederholte Ausführung im Intervall |
| `--interval` | Sekunden zwischen zwei Läufen im `--watch`-Modus (Default: 10) |
| `--long-queries-type` | Sortierkriterium für langsame Queries: `latency`, `cpu`, `memory` |
| `--long-queries-limit` | Anzahl der angezeigten langsamen Queries |
| `--user`, `-u` | Basic-Auth-Benutzername, falls das Security-Plugin aktiv ist (Default: `.env`/`$OPENSEARCH_USER`) |
| `--password` | Basic-Auth-Passwort (Default: `.env`/`$OPENSEARCH_PASSWORD`); bei `--user` ohne Passwort wird interaktiv nachgefragt |
| `--ca-cert` | Pfad zu einem CA-Bundle für ein selbstsigniertes TLS-Zertifikat (Default: `.env`/`$OPENSEARCH_CA_CERT`) |
| `--insecure`, `-k` | TLS-Zertifikatsprüfung überspringen (Default: `.env`/`$OPENSEARCH_INSECURE`); nur für lokale/Dev-Setups |

## adduser.sh

```bash
./adduser.sh                                  # gegen den lokalen Compose-Stack, Passwort aus .env
./adduser.sh --host https://prod:9200 --admin-password '...'   # gegen einen anderen Cluster
./adduser.sh --user my-monitor --role my-monitor-role
```

| Option | Beschreibung |
|---|---|
| `--host` | OpenSearch-Basis-URL (Default: `.env`/`$OPENSEARCH_HOST` oder `https://localhost:9200`) |
| `--admin-user` | Admin-Benutzer für die Security-API (Default: `admin`) |
| `--admin-password` | Admin-Passwort (Default: `.env`/`$OPENSEARCH_INITIAL_ADMIN_PASSWORD`, sonst Prompt) |
| `--user` | Anzulegender Analyzer-User (Default: `opensearch-analyzer`) |
| `--password` | Festes Passwort statt eines generierten (nicht empfohlen) |
| `--role` | Name der anzulegenden Rolle (Default: `opensearch-analyzer-monitor`) |
| `--insecure` / `--no-insecure` | TLS-Zertifikatsprüfung überspringen/erzwingen (Default: `.env`/`$OPENSEARCH_INSECURE`) |

## stress.sh

Aktiviert das [Query-Insights-Plugin](https://opensearch.org/docs/latest/observing-your-data/query-insights/index/)
(`/_insights/top_queries`), seedet einen Testindex und feuert parallel absichtlich
teure Queries (Wildcard, Regexp, Script-Score, High-Cardinality-Aggregationen,
Deep Pagination, Fuzzy-Match), damit `opensearch-analyze.py --long-queries-*`
reale Daten anzeigt. Legt Indizes an und schreibt Dokumente, braucht daher
Admin-/Write-Zugangsdaten (Default: `admin` mit `$OPENSEARCH_INITIAL_ADMIN_PASSWORD`
aus `.env`) — nicht den read-only User von `adduser.sh`.

```bash
./stress.sh                                   # Standardlauf (20000 Docs, 20 Runden)
./stress.sh --docs 2000 --rounds 5            # kleinerer/schnellerer Lauf
./stress.sh --skip-seed --rounds 10           # Index bereits vorhanden, nur Queries feuern
./stress.sh --cleanup                         # Testindex wieder löschen
```

| Option | Beschreibung |
|---|---|
| `--host` | OpenSearch-Basis-URL (Default: `.env`/`$OPENSEARCH_HOST` oder `http://localhost:9200`) |
| `--index` | Name des Testindex (Default: `stress-test-logs`) |
| `--docs` | Anzahl generierter Dokumente (Default: 20000) |
| `--rounds` | Query-Runden pro Query-Typ (Default: 20) |
| `--parallel` | Parallele Requests je Runde (Default: 8) |
| `--user` | Basic-Auth-Benutzer (Default: `admin`) |
| `--password` | Basic-Auth-Passwort (Default: `.env`/`$OPENSEARCH_INITIAL_ADMIN_PASSWORD`) |
| `--insecure` | TLS-Zertifikatsprüfung überspringen (Default: `.env`/`$OPENSEARCH_INSECURE`) |
| `--skip-seed` | Indexierung überspringen, nur Queries feuern |
| `--cleanup` | Testindex löschen und beenden |

`delete.sh` löscht denselben Testindex eigenständig und akzeptiert dieselben
`--host`/`--user`/`--password`/`--insecure`-Optionen.

Ergebnisse danach mit:
```bash
./opensearch-analyze.py --long-queries-type latency --long-queries-limit 10
./opensearch-analyze.py --long-queries-type cpu --long-queries-limit 10
```

## Referenzen

- [OpenSearch Documentation](https://opensearch.org/docs/latest/)
- [OpenSearch Cluster Health API](https://opensearch.org/docs/latest/api-reference/cluster-api/cluster-health/)
- [OpenSearch Cluster Formation](https://opensearch.org/docs/latest/tuning-your-cluster/)
- [OpenSearch Query Insights Plugin](https://opensearch.org/docs/latest/observing-your-data/query-insights/index/)
- [OpenSearch Dashboards](https://opensearch.org/docs/latest/dashboards/index/)
- [Fluent Bit Documentation](https://docs.fluentbit.io/manual)
- [Fluent Bit OpenSearch Output Plugin](https://docs.fluentbit.io/manual/pipeline/outputs/opensearch)
