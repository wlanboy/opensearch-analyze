import ssl

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
