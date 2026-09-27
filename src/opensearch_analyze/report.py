"""Renders the text report to stdout."""

import textwrap
from datetime import datetime

from .constants import QUERY_COLUMN_WRAP
from .findings import FindingsBuilder
from .formatting import Formatter
from .i18n import Translator


class ReportPrinter:
    @staticmethod
    def format_ratio(ratio: float | None) -> str:
        return "-" if ratio is None else f"{ratio:.1f}"

    @staticmethod
    def print_table(lang: str, headers: list, rows: list, wrap_widths: dict | None = None) -> None:
        """Print an aligned table. wrap_widths maps column index -> max width;
        cells in that column wrap onto continuation lines instead of being cut off."""
        if not rows:
            print(f"  {Translator.t(lang, 'table_empty')}")
            return
        wrap_widths = wrap_widths or {}

        def cell_lines(cell, col_idx):
            text = str(cell)
            limit = wrap_widths.get(col_idx)
            if limit:
                return textwrap.wrap(text, width=limit) or [""]
            return [text]

        wrapped_rows = [[cell_lines(cell, i) for i, cell in enumerate(row)] for row in rows]

        widths = [len(h) for h in headers]
        for cols_lines in wrapped_rows:
            for i, lines in enumerate(cols_lines):
                widths[i] = max([widths[i]] + [len(line) for line in lines])

        def fmt_row(cells):
            return "  " + "  ".join(str(c).ljust(widths[i]) for i, c in enumerate(cells))

        print(fmt_row(headers))
        print(fmt_row(["-" * w for w in widths]))
        for cols_lines in wrapped_rows:
            for line_idx in range(max(len(lines) for lines in cols_lines)):
                cells = [lines[line_idx] if line_idx < len(lines) else "" for lines in cols_lines]
                print(fmt_row(cells))

    @classmethod
    def print_report(cls, lang: str, host: str, cluster: dict, indices: list | None, nodes: list | None,
                      top_queries, query_limit: int, watermarks: dict | None, findings: list,
                      collection_errors: dict | None = None, query_type: str = "latency") -> None:
        collection_errors = collection_errors or {}
        watermarks = watermarks or {}
        ts = datetime.now().astimezone().strftime("%Y-%m-%d %H:%M:%S")
        print(Translator.t(lang, "report_header", host=host, ts=ts))
        print("=" * 70)

        def fmt_wm(key):
            return FindingsBuilder.format_watermark(lang, watermarks, key) or "n/a"

        print(Translator.t(lang, "watermarks_line", low=fmt_wm("low"), high=fmt_wm("high"),
                            flood=fmt_wm("flood_stage")))

        print(f"\n{Translator.t(lang, 'title_cluster')}")
        cls.print_table(
            lang,
            Translator.headers(lang, "headers_cluster"),
            [[
                cluster.get("status"), cluster.get("number_of_nodes"), cluster.get("active_shards"),
                cluster.get("relocating_shards"), cluster.get("initializing_shards"),
                cluster.get("unassigned_shards"),
            ]],
        )

        if indices is None:
            print(f"\n{Translator.t(lang, 'title_indices')}")
            print(f"  {collection_errors.get('indices', '')}")
        else:
            print(f"\n{Translator.t(lang, 'section_indices', n=len(indices))}")
            cls.print_table(
                lang,
                Translator.headers(lang, "headers_indices"),
                [[
                    i["index"], i["docs_count"], Formatter.format_bytes(i["store_size_bytes"]), i["query_total"],
                    Formatter.format_ms(i["avg_query_ms"]), i["indexing_total"], i["indexing_failed"],
                    cls.format_ratio(i["query_cache_hit_ratio"]), cls.format_ratio(i["request_cache_hit_ratio"]),
                ] for i in indices],
            )

        if nodes is None:
            print(f"\n{Translator.t(lang, 'title_nodes')}")
            print(f"  {collection_errors.get('nodes', '')}")
        else:
            print(f"\n{Translator.t(lang, 'section_nodes', n=len(nodes))}")
            cls.print_table(
                lang,
                Translator.headers(lang, "headers_nodes"),
                [[
                    n["node"], n["search_queue"], n["search_rejected"], n["search_active"], n["heap_used_percent"],
                    f"{n['disk_used_percent']:.1f}",
                    n["breaker_parent_tripped"] + n["breaker_fielddata_tripped"] + n["breaker_request_tripped"],
                ] for n in nodes],
            )

        metric = Translator.t(lang, f"metric_{query_type}")
        if query_limit > 0 and top_queries is None:
            print(f"\n{Translator.t(lang, 'title_long_queries')}")
            reason = collection_errors.get("top_queries") \
                or Translator.t(lang, "long_queries_plugin_unavailable_msg", metric=metric, type=query_type)
            print(f"  {reason}")
        elif query_limit > 0:
            print(f"\n{Translator.t(lang, 'section_long_queries', n=len(top_queries), metric=metric)}")
            cls.print_table(
                lang,
                Translator.headers(lang, "headers_long_queries"),
                [[
                    Formatter.format_ms(q["latency_ms"]), Formatter.format_ms(q["cpu_ns"] / 1_000_000),
                    Formatter.format_bytes(q["memory_bytes"]), q["indices"], q["total_shards"], q["search_type"],
                    Formatter.format_timestamp_ms(q["timestamp_ms"]), q["query"],
                ] for q in top_queries],
                wrap_widths={7: QUERY_COLUMN_WRAP},
            )

        print(f"\n{Translator.t(lang, 'section_findings', n=len(findings))}")
        if findings:
            for f in findings:
                print(f"  ! {f}")
        else:
            print(f"  {Translator.t(lang, 'no_issues')}")
        print()
