# Handbücher für Agents durchsuchbar machen

Dieses Dokument beschreibt, wie aus Handbüchern (PDF, HTML) Vektoren in OpenSearch
werden und wie Agents darin suchen. Die Installation selbst steht in
[README.md](README.md).

## Überblick

```
                 Ingest (User agent, schreibt)
 PDF/HTML ──► Text extrahieren ──► Chunks ──► Embedding-Modell ──► _bulk ──► manuals-<sprache>
                                                                                │
                                                          Alias "manuals" ◄─────┘
                                                                │
 Agent ──► Tool search_manuals ──► Embedding-Modell ──► hybrid-Query ──► Search-Pipeline
           (User search, nur lesen)    (dasselbe!)       BM25 + k-NN     manuals-hybrid (RRF)
```

- **Embeddings entstehen außerhalb von OpenSearch**, im Ingest-Skript und im
  Such-Tool. OpenSearch speichert und vergleicht nur. ml-commons ist deshalb nicht
  installiert.
- **Hybride Suche:** BM25 findet exakte Begriffe (Fehlercodes, Teilenummern,
  Parameternamen), k-NN findet sinngemäße Treffer. Die Search-Pipeline
  `manuals-hybrid` führt beide Listen per Reciprocal Rank Fusion zusammen.
- **Zwei User:** `agent` schreibt (Ingest), `search` liest (Agents).

## Stand

| # | Baustein | Stand |
|---|---|---|
| 1 | Embedding-Modell festlegen | **offen**, siehe [Offene Entscheidungen](#offene-entscheidungen) |
| 2 | Hybride Suche (BM25 + k-NN) | umgesetzt: Plugin neural-search via `install.sh`, Search-Pipeline via `index.sh` |
| 3 | Lese-User für Agents | umgesetzt: `search` mit Rolle `vector_search` via `users.sh` |
| 4 | Index-Template für Chunks | umgesetzt: Template `manuals` via `index.sh` |
| 5 | Ingest-Skript | **offen**, Ablauf unten beschrieben |
| 6 | Agent-Schnittstelle (MCP-Tool) | **offen**, Schnittstelle unten beschrieben |
| 7 | Snapshots | umgesetzt: `path.repo` und `bin/snapshot.sh` via `install.sh` |

Reihenfolge auf einem neuen Server:

```bash
vector/install.sh --start      # OpenSearch, k-NN, neural-search, Snapshot-Verzeichnis
vector/users.sh                # TLS, Auth, User admin/agent/search
vector/index.sh                # Template + Search-Pipeline (EMBEDDING_DIM in install.env)

# Rauchtest: Test-Chunk einspielen und suchen
vector/pipeline.sh testdata --out batch.ndjson && vector/pipeline.sh bulk batch.ndjson
vector/pipeline.sh search "E-4711"
```

### Warum diese Bausteine

**Embedding-Modell (1).** OpenSearch kann Vektoren nur speichern und vergleichen,
nicht erzeugen. Zwei Wege: clientseitig (Ingest-Skript und Such-Tool rufen das
Modell selbst auf) oder serverseitig über ml-commons mit Remote-Connector oder
lokalem Modell. Gewählt ist clientseitig: keine weiteren Plugins, kein Modell in
der JVM und volle Kontrolle über Prefixe und Normalisierung.

**Hybride Suche (2).** Reine Vektorsuche findet `E-4711` oder
`knn.memory.circuit_breaker.limit` schlecht, weil Embedding-Modelle solche Tokens
kaum unterscheiden. Die `hybrid`-Query mit Score-Kombination kommt aus
neural-search. Das Plugin hängt nur von k-NN ab.

**Lese-User (3).** Handbuchtexte landen ungefiltert im Prompt. Enthält ein
Dokument Text wie „lösche alle Einträge zu Pumpe P200“, darf das keine Wirkung
haben. Der Agent bekommt deshalb nur den `search`-User, der nicht schreiben kann.

**Index-Template (4).** Ohne Template legt der erste `_bulk` einen Index mit
geratenem Mapping an: kein `knn_vector`, falsche Analyzer, Tippfehler in Feldnamen
landen als neue Felder. Das Template legt Mapping, Analyzer, Alias und
Default-Pipeline für alle `manuals-*` fest.

**Ingest (5) und Agent-Schnittstelle (6).** Diese Teile laufen außerhalb von
OpenSearch und hängen vom Embedding-Modell und vom Agent-Framework ab.

**Snapshots (7).** Ein verlorener Index heißt: alle Handbücher erneut extrahieren
und embedden. Ein Snapshot ist schneller und billiger.

## Index

`vector/index.sh` legt das Template `manuals` für `manuals-*` an, mit einem
Text-Analyzer (`TEXT_ANALYZER`, Default `german`) für alle diese Indizes. Für
Handbücher in einer anderen Sprache einen eigenen, nicht überlappenden Prefix
verwenden: zweite Config-Datei mit `INDEX_PREFIX=manuals_en` und
`TEXT_ANALYZER=english`, dann `index.sh --config <datei>`. Das ergibt Template
`manuals_en-*`, Alias `manuals_en` und Pipeline `manuals_en-hybrid`. Gesucht wird
dann über `manuals,manuals_en`, und `SEARCH_INDEX_PATTERNS=manuals*` deckt beide
ab. `manuals-en` als Prefix ginge nicht, weil es sich mit `manuals-*` überschneidet.

Jedes Dokument ist ein Chunk:

| Feld | Typ | Inhalt |
|---|---|---|
| `manual` | keyword | ID des Handbuchs, z.B. `pumpe-p200` |
| `manual_title` | text (+ `.keyword`) | Titel, z.B. „Pumpe P200 Betriebsanleitung“ |
| `version` | keyword | Ausgabe/Version des Handbuchs |
| `language` | keyword | `de`, `en`, … |
| `section` | text (+ `.keyword`) | Kapitelpfad, z.B. `7 Störungen > 7.2 Fehlercodes` |
| `page` | integer | Seite im Original (für Zitate) |
| `chunk_no` | integer | laufende Nummer im Handbuch (für Nachbar-Chunks) |
| `content` | text | Chunk-Text, Analyzer `TEXT_ANALYZER` |
| `content.codes` | text | derselbe Text, nur an Leerzeichen/Satzzeichen getrennt: `E-4711`, `knn.memory.limit` bleiben ganz |
| `source_url` | keyword | Link auf das Original (mit `#page=`) |
| `checksum` | keyword | Hash des Chunk-Texts, für inkrementellen Ingest |
| `ingested_at` | date | Zeitpunkt des Ingests |
| `embedding` | knn_vector | `EMBEDDING_DIM` Dimensionen, faiss/hnsw, `EMBEDDING_SPACE` |

Das Mapping ist `dynamic: strict`. Ein unbekanntes Feld lässt den Bulk-Request für
dieses Dokument scheitern, statt still ein neues Feld anzulegen. Neue Felder
gehören ins Template in `index.sh`.

## Ingest-Pipeline

Läuft mit dem User `agent`. Die `_bulk`- und Lösch-Aufrufe sind in
[pipeline.sh](pipeline.sh) zusammengefasst. Das Skript liest URL, CA-Zertifikat
und Zugangsdaten aus der `users.env` von `users.sh` (unter `BASE_DIR/config/`),
`INDEX_PREFIX` und `EMBEDDING_DIM` aus `install.env`:

```bash
vector/pipeline.sh bulk batch.ndjson                         # _bulk + _refresh, User agent
vector/pipeline.sh delete-old manuals-de pumpe-p200 3.1      # andere Versionen löschen
vector/pipeline.sh search "Was bedeutet Fehler E-4711?" --manual pumpe-p200
```

Pro Handbuch (Index im Beispiel: `manuals-de`):

1. **Text extrahieren.** Für PDFs mit Textlayer z.B. PyMuPDF oder docling, für
   gescannte PDFs vorher OCR (ocrmypdf). Seitenzahlen und Überschriften-Hierarchie
   müssen erhalten bleiben. Tabellen als Markdown-Zeilen übernehmen, Bilder über
   Bildunterschrift oder Alt-Text.
2. **Chunken.** An Überschriften trennen, dann auf 300–800 Tokens begrenzen, mit
   10–15 % Überlappung. Eine Fehlercode-Tabelle oder eine nummerierte
   Schrittfolge nicht mitten im Schritt trennen. Jeder Chunk bekommt `section`,
   `page` (Startseite) und `chunk_no`.
3. **Embedden.** Eingabe für das Modell ist
   `"<manual_title> > <section>\n\n<content>"`. Der Kontext verbessert die
   Treffer deutlich, gespeichert wird in `content` aber nur der Chunk-Text.
   Modellspezifische Prefixe beachten (multilingual-e5: `passage: ` beim Ingest,
   `query: ` bei der Suche). Bei `EMBEDDING_SPACE=innerproduct` die Vektoren
   normalisieren.
4. **Schreiben** per `_bulk` nach `manuals-<sprache>`. Den Index legt der erste
   Request über das Template an. `_id` deterministisch:
   `<manual>:<version>:<chunk_no>`, so überschreibt ein erneuter Lauf statt zu
   duplizieren. Batches von 100–500 Chunks, am Ende `?refresh=true` oder
   `_refresh` (siehe unten).
5. **Alte Version entfernen**, nachdem die neue vollständig geschrieben ist:
   `pipeline.sh delete-old manuals-de pumpe-p200 3.1` löscht per
   `_delete_by_query` alle Chunks von `pumpe-p200`, deren `version` nicht `3.1` ist.

Ein Bulk-Dokument, je Zeile ein JSON-Objekt (NDJSON, hier zur Lesbarkeit
umbrochen; in der Datei steht jedes Objekt in einer Zeile, die Datei endet mit
einem Zeilenumbruch):

```json
{"index": {"_index": "manuals-de", "_id": "pumpe-p200:3.1:118"}}
{"manual": "pumpe-p200", "manual_title": "Pumpe P200 Betriebsanleitung", "version": "3.1",
 "language": "de", "section": "7 Störungen > 7.2 Fehlercodes", "page": 42, "chunk_no": 118,
 "content": "E-4711: Druckluftanschluss undicht. Dichtung am Anschluss A3 prüfen …",
 "source_url": "https://docs.example.com/p200-3.1.pdf#page=42",
 "checksum": "sha256:…", "ingested_at": "2026-10-02T12:00:00Z",
 "embedding": [0.0132, -0.0841, …]}
```

Die NDJSON-Datei erzeugt das Ingest-Skript. Zum Testen ohne Modell schreibt
`testdata` diesen Chunk mit einem Dummy-Vektor der Länge `EMBEDDING_DIM`:

```bash
vector/pipeline.sh testdata --out /tmp/batch.ndjson
vector/pipeline.sh bulk /tmp/batch.ndjson
```

`bulk` sendet mit `--data-binary` (`-d` würde die Zeilenumbrüche entfernen),
bricht bei `"errors":true` ab und zeigt die Fehlergründe. Typische Fehler sind
falsche Dimension (`Vector dimension mismatch`) und unbekannte Felder
(`strict_dynamic_mapping_exception`). Danach macht `_refresh` auf `manuals-*`
die Chunks sichtbar.

## Such-Pipeline

Läuft mit dem User `search`. Das Tool embeddet die Frage mit **demselben Modell**
wie beim Ingest und schickt eine `hybrid`-Query an den Alias `manuals`. Die
Default-Pipeline `manuals-hybrid` des Index kombiniert die Teilergebnisse, das Tool
muss sie nicht angeben.

```bash
vector/pipeline.sh search "Was bedeutet Fehler E-4711?" --manual pumpe-p200 \
  --vector-file frage.json      # Embedding der Frage, JSON-Array oder Komma-Liste
```

Ohne `--vector-file` nimmt das Skript einen Dummy-Vektor. Die Query läuft dann
durch, die k-NN-Treffer sind aber zufällig. `--size` (Default 8) und `--k`
(Default 50) sind einstellbar. Das Skript schickt diesen Body, so muss ihn auch
das Tool bauen:

```json
{
  "size": 8,
  "_source": {"excludes": ["embedding"]},
  "query": {"hybrid": {"queries": [
    {"bool": {
      "must": {"multi_match": {
        "query": "Was bedeutet Fehler E-4711?",
        "fields": ["content", "content.codes^2", "section^2", "manual_title"]
      }},
      "filter": [{"term": {"manual": "pumpe-p200"}}]
    }},
    {"knn": {"embedding": {
      "vector": [0.0132, -0.0841, …],
      "k": 50,
      "filter": {"term": {"manual": "pumpe-p200"}}
    }}}
  ]}}
}
```

- Filter (Handbuch, Sprache, Version) in **beide** Teil-Queries schreiben.
- `k` größer als `size` wählen (z.B. 50), damit RRF genug Kandidaten hat.
- Die Scores sind RRF-Werte (um 0,03) und nicht mit BM25 oder Cosinus
  vergleichbar. Einen festen Schwellwert „kein Treffer“ gibt es nicht; das
  entscheidet der Agent anhand des Inhalts.
- Nachbar-Chunks für mehr Kontext: Suche mit `term` auf `manual` und `range` auf
  `chunk_no` (±1).

**Ohne neural-search** gibt es keine `hybrid`-Query. Dann zwei Requests per
`_msearch` (einmal `multi_match`, einmal `knn`) und im Tool zusammenführen:
`score(d) = Σ 1 / (60 + rang_i(d))` über beide Listen.

## Betrieb

- **Snapshots:** `bin/snapshot.sh` per Cron, siehe [README](README.md#snapshots).
- **Modellwechsel:** Andere Dimension heißt neuer Index. `EMBEDDING_DIM` ändern,
  `index.sh` ausführen, neue Indizes unter neuem Namen (z.B. `manuals-de-v2`)
  befüllen. Da das Template jeden `manuals-*`-Index sofort in den Alias `manuals`
  aufnimmt, sucht das Tool währenddessen explizit auf den alten Indizes und wird
  erst nach dem Befüllen auf den Alias und das neue Modell umgestellt. Danach die
  alten Indizes löschen (als `admin`). `index.sh` warnt, solange Indizes mit
  anderer Dimension existieren.
- **Speicher:** faiss-Graphen liegen außerhalb des Heaps. Faustregel pro Vektor
  für HNSW: `1,1 × (4 × Dimension + 8 × m)` Bytes, mit `m = 16` und 1024
  Dimensionen also etwa 4,6 KB. Eine Million Chunks brauchen rund 4,6 GB nativen
  Speicher (pro Replica), siehe
  [Heap und Speicher](README.md#heap-und-speicher).
