import logging
from xml.etree import ElementTree as ET

from defusedxml.ElementTree import fromstring  # type: ignore[import-untyped]
from tenacity import RetryCallState, retry, retry_if_exception_type, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)


def _httpx():
    import httpx

    return httpx


DEFAULT_TIMEOUT = _httpx().Timeout(connect=30.0, read=60.0, write=30.0, pool=30.0)


def _log_retry(retry_state: RetryCallState) -> None:
    logger.warning(
        "Retrying %s (attempt %d) after %s",
        retry_state.fn.__name__ if retry_state.fn else "unknown",
        retry_state.attempt_number,
        retry_state.outcome.exception() if retry_state.outcome else "unknown",
    )


class CongresoClientError(Exception):
    pass


class CongresoAPIError(CongresoClientError):
    def __init__(self, message: str, status_code: int | None = None, url: str = ""):
        self.status_code = status_code
        self.url = url
        super().__init__(message)


class CongresoParseError(CongresoClientError):
    pass


class BaseCongresoClient:
    BASE_URL: str = ""

    def __init__(self, timeout=None):
        self._timeout = timeout or DEFAULT_TIMEOUT
        self._client = None

    @property
    def client(self):
        httpx = _httpx()
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                timeout=self._timeout,
                follow_redirects=True,
                headers={
                    "User-Agent": "CamaraAbierta/1.0 (+https://camaraabierta.cl)",
                    "Accept": "application/xml, text/xml, */*",
                },
            )
        return self._client

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    @retry(
        retry=retry_if_exception_type((_httpx().TransportError, _httpx().TimeoutException)),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=2, max=30),
        before_sleep=_log_retry,
        reraise=True,
    )
    def _get(self, url: str, params: dict | None = None):
        full_url = f"{self.BASE_URL}{url}" if not url.startswith("http") else url
        response = self.client.get(full_url, params=params)
        if response.status_code != 200:
            raise CongresoAPIError(
                f"HTTP {response.status_code} from {full_url}",
                status_code=response.status_code,
                url=full_url,
            )
        return response

    def _get_xml(self, url: str, params: dict | None = None) -> ET.Element:
        response = self._get(url, params=params)
        try:
            return fromstring(response.content)
        except ET.ParseError as exc:
            raise CongresoParseError(f"XML parse error from {url}: {exc}") from exc

    @staticmethod
    def _text(element: ET.Element | None, tag: str) -> str:
        if element is None:
            return ""
        child = element.find(tag)
        if child is not None and child.text:
            return child.text.strip()
        return ""

    @staticmethod
    def _parse_date_dmy(value: str) -> str | None:
        if not value:
            return None
        import re

        match = re.match(r"(\d{2})/(\d{2})/(\d{4})", value)
        if match:
            return f"{match.group(3)}-{match.group(2)}-{match.group(1)}"
        if re.match(r"\d{4}-\d{2}-\d{2}", value):
            return value
        return None