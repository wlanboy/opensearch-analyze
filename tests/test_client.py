import io
import ssl
from urllib.error import URLError

import pytest

from opensearch_analyze import client as client_module
from opensearch_analyze.client import OpenSearchClient


def test_basic_auth_header():
    client = OpenSearchClient("http://x", username="u", password="p")
    assert client._auth_header == "Basic dTpw"  # base64("u:p")


def test_api_key_with_colon_is_base64_encoded():
    client = OpenSearchClient("http://x", api_key="id:secret")
    assert client._auth_header == "ApiKey aWQ6c2VjcmV0"  # base64("id:secret")


def test_api_key_without_colon_is_passed_through():
    client = OpenSearchClient("http://x", api_key="already-encoded-token")
    assert client._auth_header == "ApiKey already-encoded-token"


def test_bearer_token_header():
    client = OpenSearchClient("http://x", bearer_token="jwt.token.here")
    assert client._auth_header == "Bearer jwt.token.here"


def test_basic_auth_takes_priority_over_others():
    client = OpenSearchClient("http://x", username="u", password="p",
                               api_key="id:secret", bearer_token="jwt")
    assert client._auth_header == "Basic dTpw"


def test_no_auth_header_without_credentials():
    client = OpenSearchClient("http://x")
    assert client._auth_header is None


def test_no_tls_context_for_plain_http():
    client = OpenSearchClient("http://x", verify_tls=False)
    assert client._ssl_context is None


def test_insecure_https_uses_public_ssl_api_and_disables_verification():
    client = OpenSearchClient("https://x", verify_tls=False)
    assert isinstance(client._ssl_context, ssl.SSLContext)
    assert client._ssl_context.check_hostname is False
    assert client._ssl_context.verify_mode == ssl.CERT_NONE


def test_verified_https_has_no_special_context():
    client = OpenSearchClient("https://x", verify_tls=True)
    assert client._ssl_context is None


def test_has_credentials_flag():
    assert OpenSearchClient("http://x", username="u", password="p").has_credentials is True
    assert OpenSearchClient("http://x").has_credentials is False


class _Response:
    def __init__(self, read):
        self.read = read

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _raise(exc):
    def read(*_args):
        raise exc
    return read


@pytest.mark.parametrize("exc", [TimeoutError("timed out"), ConnectionResetError("reset")])
def test_get_wraps_errors_while_reading_body_in_urlerror(monkeypatch, exc):
    monkeypatch.setattr(client_module, "urlopen", lambda *a, **k: _Response(_raise(exc)))
    with pytest.raises(URLError) as excinfo:
        OpenSearchClient("http://x").get("/")
    assert excinfo.value.reason is exc


def test_get_wraps_invalid_json_in_urlerror(monkeypatch):
    monkeypatch.setattr(client_module, "urlopen", lambda *a, **k: _Response(io.BytesIO(b"<html>").read))
    with pytest.raises(URLError) as excinfo:
        OpenSearchClient("http://x").get("/")
    assert isinstance(excinfo.value.reason, ValueError)


def test_get_returns_decoded_json(monkeypatch):
    monkeypatch.setattr(client_module, "urlopen", lambda *a, **k: _Response(io.BytesIO(b'{"a": 1}').read))
    assert OpenSearchClient("http://x").get("/") == {"a": 1}
