# Anleitung: Handbücher in einem Dashboards-Workspace durchsuchen

Diese Anleitung richtet in OpenSearch Dashboards einen Workspace ein, in dem man
die Handbuch-Chunks aus `manuals-*` per Volltext durchsuchen kann. Grundlage sind
[dashboards.sh](dashboards.sh) und der Index aus [index.sh](index.sh) bzw.
[pipeline.md](pipeline.md).

Ergebnis:

- Data Source `local` zeigt auf den OpenSearch-Cluster
- Workspace `manuals` vom Typ **Search** mit dieser Data Source
- Index Pattern `manuals-*` im Workspace
- Suche in Discover mit DQL über `content`, `section`, `manual_title` usw.

Die Beispiele gehen von Dashboards unter `https://localhost:5601` und OpenSearch
unter `https://localhost:9200` aus.

---

## Schritt 0: Voraussetzungen prüfen

`dashboards.sh` schreibt diese Einstellungen nach `opensearch_dashboards.yml`:

```yaml
data_source.enabled: true          # Multiple Data Sources
workspace.enabled: true            # Workspaces
explore.enabled: true
# nur mit Auth/TLS (users.sh):
data_source.ssl.verificationMode: full
data_source.ssl.certificateAuthorities: ["<CERT_DIR>/root-ca.pem"]
```

Ohne die beiden `data_source.ssl.*`-Zeilen kennt der Data-Source-Client die
eigene Root-CA nicht, und *Test connection* scheitert am Zertifikat. Nach
Änderungen `dashboards.sh` erneut ausführen und Dashboards neu starten.

Der Index muss Daten enthalten:

```bash
set -a; . /opt/local/opensearch/config/users.env; set +a
curl -s --cacert "$OPENSEARCH_CACERT" -u "$OPENSEARCH_SEARCH_USER:$OPENSEARCH_SEARCH_PASSWORD" \
  "$OPENSEARCH_URL/manuals-*/_count"
```

## Schritt 1: Data Source anlegen

Workspaces arbeiten nur mit Data Sources. Der Cluster aus `opensearch.hosts`
("Local Cluster") ist keine Data Source und lässt sich keinem Workspace zuordnen.
Deshalb legt man für denselben Cluster einmal eine Data Source an.

1. Außerhalb eines Workspaces auf der Startseite *Data administration* → *Data sources* öffnen.
2. *Create data source* → *OpenSearch*.
3. Ausfüllen:

   | Feld | Wert |
   |---|---|
   | Title | `local` |
   | Endpoint URL | `https://localhost:9200` |
   | Authentication | Username & Password |
   | Username / Password | `OPENSEARCH_SEARCH_USER` / `OPENSEARCH_SEARCH_PASSWORD` aus `users.env` |

4. *Test connection* und danach *Connect to OpenSearch Cluster*.

Der Hostname im Endpoint muss im Node-Zertifikat stehen (`verificationMode: full`).
`localhost`, `127.0.0.1` und der Hostname der VM sind drin, weitere per
`EXTRA_SANS` in `users.sh`.

Der Search-User darf nur lesen. Dashboards fragt beim Anlegen zusätzlich Version
und Plugins des Clusters ab (`GET /`, `_cat/plugins`). Die Rechte dafür
(`cluster:monitor/main`, `cluster:monitor/nodes/info`, `cluster:monitor/state`)
hat die Rolle `vector_search` seit der Erweiterung in `users.sh`. Bei einer
älteren Installation `users.sh` erneut ausführen, sonst scheitert *Test
connection* mit 403.

Im Workspace sieht man mit diesem Account nur die Indizes aus
`SEARCH_INDEX_PATTERNS`.

## Schritt 2: Workspace anlegen

1. Auf der Startseite *Workspaces* → *Create workspace*.
2. Name: `manuals`.
3. Use case: **Search**.
4. Data Source `local` zuordnen. Wird das nicht schon im Formular angeboten:
   nach dem Anlegen im Workspace *Data sources* → *Associate data sources* → `local`.

Nicht **Observability** wählen. Dort zeigt Discover die neue Explore-Ansicht, die
nur PPL und SQL kennt. Beides braucht das Plugin `opensearch-sql`, das
`install.sh` nur mit `SQL=auto|true` und vorhandenem Zip installiert.

Eine Data Source muss jedem Workspace einzeln zugeordnet werden. Ein neuer
Workspace sieht keine.

## Schritt 3: Index Pattern anlegen

Discover sucht über ein Index Pattern. Wählt man im Data-Dialog stattdessen
direkt *Indexes*, bietet Dashboards nur PPL und SQL an.

1. Im Workspace `manuals` *Index patterns* öffnen (in den Workspace-Einstellungen
   bzw. unter *Assets*).
2. *Create index pattern*, Data Source `local`.
3. Pattern: `manuals-*`. Nicht `manuals*`, das trifft zusätzlich den Alias `manuals`.
4. Zeitfeld: **keins** ("I don't want to use the time filter").

Ohne Zeitfeld zeigt Discover immer alle Chunks. Mit `ingested_at` als Zeitfeld
filtert Discover nach dem Einspielzeitpunkt (Default: letzte 15 Minuten), und
ältere Handbücher fehlen scheinbar in der Trefferliste.

Alternativ per API. Workspace-ID (`/w/<id>/` in der URL) und Data-Source-ID
anpassen:

```bash
set -a; . /opt/local/opensearch/config/users.env; set +a
curl -s --cacert "$OPENSEARCH_CACERT" -u "$OPENSEARCH_ADMIN_USER:$OPENSEARCH_ADMIN_PASSWORD" \
  -H 'osd-xsrf: true' -H 'Content-Type: application/json' \
  -X POST "https://localhost:5601/w/<workspace-id>/api/saved_objects/index-pattern" \
  -d '{
    "attributes": {"title": "manuals-*"},
    "references": [{"id": "<data-source-id>", "type": "data-source", "name": "dataSource"}]
  }'
```

Die IDs aller Workspaces liefert:

```bash
curl -s --cacert "$OPENSEARCH_CACERT" -u "$OPENSEARCH_ADMIN_USER:$OPENSEARCH_ADMIN_PASSWORD" \
  -H 'osd-xsrf: true' -H 'Content-Type: application/json' \
  -X POST https://localhost:5601/api/workspaces/_list -d '{}'
```

## Schritt 4: In Discover suchen

1. Im Workspace `manuals` *Discover* öffnen.
2. Im Data-Dialog unter *Index patterns* `manuals-*` wählen.
3. Sprache **DQL** (oder Lucene).
4. Links die Felder `manual_title`, `section`, `page` und `content` als Spalten
   hinzufügen, damit nicht das ganze `_source` angezeigt wird.

Die Felder eines Chunks:

| Feld | Typ | Inhalt |
|---|---|---|
| `content` | text | Text des Chunks |
| `section` | text | Kapitelpfad, z.B. `7 Störungen > 7.2 Fehlercodes` |
| `manual_title` | text | Titel des Handbuchs |
| `manual` | keyword | Kennung, z.B. `pumpe-p200` |
| `version` | keyword | Handbuch-Version |
| `language` | keyword | Sprache, z.B. `de` |
| `page` | integer | Seite im PDF |
| `source_url` | keyword | Link auf die Seite im PDF |

Beispiele für DQL:

```text
Dichtung                                   # Volltext über alle Felder
content:Druckluft                          # nur im Chunk-Text
content:"Dichtung am Anschluss"            # Phrase
manual:pumpe-p200 and content:Fehlercode   # in einem Handbuch
section:Störungen and not version:2.0      # Kapitel, Version ausschließen
page >= 40 and page <= 45                  # Seitenbereich
```

`content`, `section` und `manual_title` laufen über den Analyzer aus
`TEXT_ANALYZER` (Default `german`). Daher findet `Dichtungen` auch `Dichtung`.
Die `keyword`-Felder (`manual`, `version`, `language`) vergleichen exakt.

Discover macht reine Textsuche (BM25). Die hybride Suche mit Embeddings
(k-NN + BM25, siehe [pipeline.md](pipeline.md)) braucht einen Query-Vektor und
ist in Discover nicht verfügbar.

## Schritt 5: Suche speichern

Spalten und Query über *Save* als gespeicherte Suche ablegen, z.B. als
`Handbuch-Suche`. Sie liegt im Workspace und steht allen zur Verfügung, die den
Workspace öffnen.

---

## Fehlerbehebung

| Symptom | Ursache | Lösung |
|---|---|---|
| Workspace zeigt keine Data Sources | Data Source nicht zugeordnet oder noch keine angelegt | Schritt 1 und 2 |
| *Test connection* scheitert mit Zertifikatsfehler | `data_source.ssl.*` fehlt oder Hostname nicht im Zertifikat | Schritt 0, Endpoint mit `localhost` |
| *Test connection* mit 403 | Rolle ohne `cluster:monitor/*` (Search-User aus älterem `users.sh`) | `users.sh` erneut ausführen |
| Nur PPL/SQL wählbar | *Indexes* statt Index Pattern gewählt oder Observability-Workspace | Schritt 3, Workspace-Typ Search |
| PPL/SQL liefert nichts | Plugin `opensearch-sql` fehlt (`/_plugins/_ppl` → "no handler found") | DQL über Index Pattern nutzen oder Plugin installieren |
| Keine Treffer trotz Daten | Index Pattern mit Zeitfeld, Zeitraum zu kurz | Zeitraum vergrößern oder Pattern ohne Zeitfeld anlegen |
