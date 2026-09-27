"""Command-line entry point: env/.env defaults, argument parsing, the run
loop, and request-error formatting."""

import argparse
import getpass
import json
import os
import ssl
import sys
import time
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError

from .client import OpenSearchClient, OpenSearchGetter
from .collectors import OpenSearchCollector
from .constants import EXIT_ERROR, EXIT_FINDINGS, EXIT_OK
from .findings import FindingsBuilder
from .i18n import Translator
from .report import ReportPrinter

# Project root of a source checkout (src/opensearch_analyze/cli.py -> ../..):
# where adduser.sh writes its .env, so the tool finds it from any directory.
PROJECT_DIR = Path(__file__).resolve().parents[2]


def dotenv_paths() -> list[Path]:
    """.env files to load, highest priority first: the current working
    directory, then the project directory."""
    paths = [Path.cwd() / ".env", PROJECT_DIR / ".env"]
    return list(dict.fromkeys(path.resolve() for path in paths))


def load_dotenv(path: Path) -> None:
    """Load KEY=VALUE lines (optionally prefixed with `export`) from a .env
    file into os.environ, without overriding variables already set — by the
    shell or by a higher-priority .env loaded earlier."""
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return
    for line in lines:
        line = line.strip()
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


def env_defaults() -> dict:
    """CLI defaults from OPENSEARCH_* environment variables, read at call
    time (after .env loading) rather than at import time."""
    env = os.environ
    lang = env.get("OPENSEARCH_LANG", "en")
    return {
        "host": env.get("OPENSEARCH_HOST", "http://localhost:9200"),
        "user": env.get("OPENSEARCH_USER") or None,
        "password": env.get("OPENSEARCH_PASSWORD") or None,
        "api_key": env.get("OPENSEARCH_API_KEY") or None,
        "bearer_token": env.get("OPENSEARCH_BEARER_TOKEN") or None,
        "ca_cert": env.get("OPENSEARCH_CA_CERT") or None,
        "insecure": env.get("OPENSEARCH_INSECURE", "").strip().lower() in ("1", "true", "yes"),
        "lang": lang if lang in ("en", "de") else "en",
    }


class AnalyzerCli:
    """Orchestrates one (or, in --watch mode, repeated) collect-analyze-print
    cycles, plus argument parsing for the console entry point."""

    @staticmethod
    def error_reason(body: str) -> str:
        """The "reason" from an OpenSearch JSON error body (which names e.g. the
        missing permission on a 403), or the raw body if it isn't one."""
        try:
            error = json.loads(body).get("error")
        except (ValueError, AttributeError):
            error = None
        if isinstance(error, dict) and error.get("reason"):
            return str(error["reason"])
        if isinstance(error, str):
            return error
        return body[:200]

    @classmethod
    def describe_request_error(cls, lang: str, client: OpenSearchGetter, exc: HTTPError | URLError) -> str:
        if isinstance(exc, HTTPError):
            if exc.code == 401:
                key = "err_auth_rejected" if client.has_credentials else "err_auth_missing"
                return Translator.t(lang, key, code=exc.code, url=exc.url)
            body = exc.read().decode(errors="replace")
            if exc.code == 403:
                return Translator.t(lang, "err_forbidden", url=exc.url, reason=cls.error_reason(body))
            return Translator.t(lang, "err_http", code=exc.code, url=exc.url, body=body[:200])
        if isinstance(exc.reason, ssl.SSLCertVerificationError):
            return Translator.t(lang, "err_tls", host=client.host, reason=exc.reason)
        if isinstance(exc.reason, ValueError):
            return Translator.t(lang, "err_invalid_response", host=client.host, reason=exc.reason)
        return Translator.t(lang, "err_unreachable", host=client.host, reason=exc.reason)

    @staticmethod
    def _collect_optional(fn, *args, **kwargs) -> tuple:
        """Run one non-critical collector method; on failure return (None, exc)
        instead of raising, so one bad section degrades gracefully rather than
        discarding every other section that already collected successfully."""
        try:
            return fn(*args, **kwargs), None
        except (HTTPError, URLError) as exc:
            return None, exc

    @classmethod
    def run_once(cls, client: OpenSearchGetter, lang: str, as_json: bool, query_type: str,
                 query_limit: int) -> int:
        collector = OpenSearchCollector(client)

        try:
            cluster = collector.cluster_health()
        except (HTTPError, URLError) as exc:
            print(cls.describe_request_error(lang, client, exc), file=sys.stderr)
            return EXIT_ERROR

        collection_errors: dict[str, str] = {}

        indices, exc = cls._collect_optional(collector.index_stats)
        if exc is not None:
            collection_errors["indices"] = cls.describe_request_error(lang, client, exc)

        nodes, exc = cls._collect_optional(collector.node_stats)
        if exc is not None:
            collection_errors["nodes"] = cls.describe_request_error(lang, client, exc)

        watermarks, exc = cls._collect_optional(collector.disk_watermarks)
        if exc is not None:
            collection_errors["disk_watermarks"] = cls.describe_request_error(lang, client, exc)
        watermarks = watermarks or {}

        top_queries = None
        if query_limit > 0:
            top_queries, exc = cls._collect_optional(collector.top_queries, query_type, query_limit)
            if exc is not None:
                collection_errors["top_queries"] = cls.describe_request_error(lang, client, exc)

        findings = FindingsBuilder.build(lang, cluster, indices or [], nodes or [], top_queries, watermarks)
        for section, reason in collection_errors.items():
            findings.append(
                Translator.t(lang, "finding_collection_failed", section=Translator.section_label(lang, section),
                              reason=reason)
            )

        if as_json:
            print(json.dumps({
                "timestamp": datetime.now().astimezone().isoformat(),
                "cluster": cluster,
                "indices": indices,
                "nodes": nodes,
                "top_queries_type": query_type,
                "top_queries": top_queries,
                "disk_watermarks": watermarks,
                "collection_errors": collection_errors,
                "findings": findings,
            }, indent=2))
        else:
            ReportPrinter.print_report(lang, client.host, cluster, indices, nodes, top_queries, query_limit,
                                        watermarks, findings, collection_errors, query_type)

        return EXIT_FINDINGS if findings else EXIT_OK

    @staticmethod
    def build_arg_parser(lang: str) -> argparse.ArgumentParser:
        defaults = env_defaults()
        parser = argparse.ArgumentParser(description=Translator.t(lang, "prog_description"))
        parser.add_argument("--host", default=defaults["host"],
                             help=Translator.t(lang, "help_host", default=defaults["host"]))
        parser.add_argument("--json", action="store_true", help=Translator.t(lang, "help_json"))
        parser.add_argument("--lang", choices=["en", "de"], default=lang,
                             help=Translator.t(lang, "help_lang", default=lang))
        parser.add_argument("--watch", action="store_true", help=Translator.t(lang, "help_watch"))
        parser.add_argument("--interval", type=float, default=10.0, help=Translator.t(lang, "help_interval"))
        parser.add_argument("--long-queries-type", choices=["latency", "cpu", "memory"], default="latency",
                             help=Translator.t(lang, "help_long_queries_type"))
        parser.add_argument("--long-queries-limit", type=int, default=10,
                             help=Translator.t(lang, "help_long_queries_limit"))
        parser.add_argument("--user", "-u", default=defaults["user"], help=Translator.t(lang, "help_user"))
        parser.add_argument("--password", default=defaults["password"], help=Translator.t(lang, "help_password"))
        parser.add_argument("--api-key", default=defaults["api_key"], help=Translator.t(lang, "help_api_key"))
        parser.add_argument("--bearer-token", default=defaults["bearer_token"],
                             help=Translator.t(lang, "help_bearer_token"))
        parser.add_argument("--ca-cert", default=defaults["ca_cert"], help=Translator.t(lang, "help_ca_cert"))
        parser.add_argument("--insecure", "-k", action="store_true", default=defaults["insecure"],
                             help=Translator.t(lang, "help_insecure"))
        return parser

    @classmethod
    def main(cls) -> int:
        for path in dotenv_paths():
            load_dotenv(path)

        lang_pre_parser = argparse.ArgumentParser(add_help=False)
        lang_pre_parser.add_argument("--lang", choices=["en", "de"], default=env_defaults()["lang"])
        pre_args, _ = lang_pre_parser.parse_known_args()

        parser = cls.build_arg_parser(pre_args.lang)
        args = parser.parse_args()
        lang = args.lang

        auth_flags = [flag for flag, value in
                      (("--user", args.user), ("--api-key", args.api_key), ("--bearer-token", args.bearer_token))
                      if value]
        if len(auth_flags) > 1:
            parser.error(Translator.t(lang, "err_auth_conflict", methods=", ".join(auth_flags)))

        password = args.password
        if args.user and not password:
            password = getpass.getpass(Translator.t(lang, "password_prompt", user=args.user))

        client = OpenSearchClient(
            args.host,
            username=args.user,
            password=password,
            api_key=args.api_key,
            bearer_token=args.bearer_token,
            verify_tls=not args.insecure,
            ca_cert=args.ca_cert,
        )

        if not args.watch:
            return cls.run_once(client, lang, args.json, args.long_queries_type, args.long_queries_limit)

        try:
            while True:
                cls.run_once(client, lang, args.json, args.long_queries_type, args.long_queries_limit)
                time.sleep(args.interval)
        except KeyboardInterrupt:
            print(f"\n{Translator.t(lang, 'stopped_message')}")
            return EXIT_OK


def main() -> int:
    return AnalyzerCli.main()


if __name__ == "__main__":
    sys.exit(main())
