# opensearch-analyze

Lokales Setup zum Betrieb und zur Analyse eines [OpenSearch](https://opensearch.org/)-Clusters:
ein Docker-Compose-Stack mit einem 2-Node-Cluster, Log-Shipping über
[Fluent Bit](https://fluentbit.io/) sowie ein Python-CLI-Tool, das Cluster-, Index- und
Node-Metriken auswertet und als Text- oder JSON-Report ausgibt.

## Bestandteile

| Komponente | Beschreibung |
|---|---|
| [docker-compose.yml](docker-compose.yml) | Startet den OpenSearch-Cluster, Fluent Bit und OpenSearch Dashboards |
| [opensearch-analyze.py](opensearch-analyze.py) | CLI-Report über Cluster-Health, Index-Stats und Node-Health |
| [conf/fluent-bit.conf](conf/fluent-bit.conf) | Fluent-Bit-Konfiguration (Syslog/Kernel-Log → OpenSearch) |
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
docker compose up -d
```

| Service | URL |
|---|---|
| OpenSearch Node 1 | http://localhost:9200 |
| OpenSearch Node 2 | http://localhost:9201 |
| OpenSearch Dashboards | http://localhost:5601 |
| Cluster-Health | http://localhost:9200/_cluster/health |

## opensearch-analyze.py

Analysiert Cluster-Health, Index-Stats (Search/Indexing/Store/Docs/Merge/Caches) und
Node-Health (Heap, Disk-Watermarks, Cache-Hit-Ratio, langsame Queries) über die OpenSearch-REST-API.

```bash
./opensearch-analyze.py --host http://localhost:9200
./opensearch-analyze.py --json
./opensearch-analyze.py --watch --interval 15
./opensearch-analyze.py --long-queries-type cpu --long-queries-limit 5
```

| Option | Beschreibung |
|---|---|
| `--host` | OpenSearch-Basis-URL (Default: `$OPENSEARCH_HOST` oder `http://localhost:9200`) |
| `--json` | Ausgabe als JSON statt Textreport |
| `--watch` | Wiederholte Ausführung im Intervall |
| `--interval` | Sekunden zwischen zwei Läufen im `--watch`-Modus (Default: 10) |
| `--long-queries-type` | Sortierkriterium für langsame Queries: `latency`, `cpu`, `memory` |
| `--long-queries-limit` | Anzahl der angezeigten langsamen Queries |

## stress.sh

Aktiviert das [Query-Insights-Plugin](https://opensearch.org/docs/latest/observing-your-data/query-insights/index/)
(`/_insights/top_queries`), seedet einen Testindex und feuert parallel absichtlich
teure Queries (Wildcard, Regexp, Script-Score, High-Cardinality-Aggregationen,
Deep Pagination, Fuzzy-Match), damit `opensearch-analyze.py --long-queries-*`
reale Daten anzeigt.

```bash
./stress.sh                                   # Standardlauf (20000 Docs, 20 Runden)
./stress.sh --docs 2000 --rounds 5            # kleinerer/schnellerer Lauf
./stress.sh --skip-seed --rounds 10           # Index bereits vorhanden, nur Queries feuern
./stress.sh --cleanup                         # Testindex wieder löschen
```

| Option | Beschreibung |
|---|---|
| `--host` | OpenSearch-Basis-URL (Default: `$OPENSEARCH_HOST` oder `http://localhost:9200`) |
| `--index` | Name des Testindex (Default: `stress-test-logs`) |
| `--docs` | Anzahl generierter Dokumente (Default: 20000) |
| `--rounds` | Query-Runden pro Query-Typ (Default: 20) |
| `--parallel` | Parallele Requests je Runde (Default: 8) |
| `--skip-seed` | Indexierung überspringen, nur Queries feuern |
| `--cleanup` | Testindex löschen und beenden |

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
