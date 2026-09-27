"""Shared test doubles for exercising collectors/cli without real network calls."""

import email.message
import io
from urllib.error import HTTPError


class FakeClient:
    """Stands in for OpenSearchClient: .get(path) returns canned data or
    raises a canned exception, keyed by path."""

    def __init__(self, responses: dict, host: str = "http://fake:9200", has_credentials: bool = False):
        self.responses = responses
        self.host = host
        self.has_credentials = has_credentials

    def get(self, path: str):
        value = self.responses[path]
        if isinstance(value, BaseException):
            raise value
        return value


def make_http_error(code: int, url: str = "http://fake:9200/x", body: bytes = b"boom") -> HTTPError:
    return HTTPError(url=url, code=code, msg="error", hdrs=email.message.Message(), fp=io.BytesIO(body))
