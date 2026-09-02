"""OpenSearch service analyzer — CLI report of cluster/index/node health.

Performs the same analysis the opensearch-metrics Spring Boot service does
(polling /_stats and /_nodes/stats) but prints a human-readable report
instead of writing documents back into OpenSearch.

Report language is selectable with --lang/$OPENSEARCH_LANG (en/de); all
report text and CLI help are translated (opensearch_analyze.i18n), the JSON
output's field names are not (they're a stable machine-readable contract).

Module layout:
  constants   thresholds and exit codes
  i18n        Translator: en/de report & CLI text
  formatting  Formatter: byte/duration/timestamp display
  client      OpenSearchClient: auth + TLS wiring over the REST API
  collectors  OpenSearchCollector: pulls cluster/index/node/query data
  findings    FindingsBuilder: turns collected data into warning strings
  report      ReportPrinter: renders the text report
  cli         AnalyzerCli: argument parsing and the collect-print run loop
"""

from .cli import main

__all__ = ["main"]
