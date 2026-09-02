"""HTTP client for the OpenSearch REST API: auth header and TLS context wiring."""

import base64
import json
import ssl
from typing import Protocol
from urllib.request import Request, urlopen


class OpenSearchGetter(Protocol):
    """What the collectors actually need from a client: a host (for error
    messages) and a GET method. Structural, so test doubles don't have to
    subclass OpenSearchClient."""
    host: str

    def get(self, path: str) -> dict: ...


class OpenSearchClient:
    """Wraps host + auth + TLS settings so collectors don't juggle them individually."""

    def __init__(self, host: str, username: str | None = None, password: str | None = None,
                 api_key: str | None = None, bearer_token: str | None = None,
                 verify_tls: bool = True, ca_cert: str | None = None, timeout: float = 10.0):
        self.host = host
        self.timeout = timeout

        self._auth_header: str | None = None
        if username:
            token = base64.b64encode(f"{username}:{password or ''}".encode()).decode()
            self._auth_header = f"Basic {token}"
        elif api_key:
            encoded = base64.b64encode(api_key.encode()).decode() if ":" in api_key else api_key
            self._auth_header = f"ApiKey {encoded}"
        elif bearer_token:
            self._auth_header = f"Bearer {bearer_token}"

        self._ssl_context: ssl.SSLContext | None = None
        if host.startswith("https://"):
            if ca_cert:
                self._ssl_context = ssl.create_default_context(cafile=ca_cert)
            elif not verify_tls:
                self._ssl_context = ssl.create_default_context()
                self._ssl_context.check_hostname = False
                self._ssl_context.verify_mode = ssl.CERT_NONE

    def get(self, path: str) -> dict:
        req = Request(f"{self.host}{path}")
        if self._auth_header:
            req.add_header("Authorization", self._auth_header)
        kwargs: dict[str, object] = {"timeout": self.timeout}
        if self._ssl_context is not None:
            kwargs["context"] = self._ssl_context
        with urlopen(req, **kwargs) as resp:  # type: ignore[arg-type]
            return json.load(resp)
