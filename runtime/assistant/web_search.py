"""Small read-only public web-search provider with auditable result metadata."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from html import unescape
from typing import Any, Callable
from urllib.parse import parse_qs, quote_plus, unquote, urlparse
from urllib.request import Request, urlopen
from xml.etree import ElementTree


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str
    domain: str
    accessed_at: str

    def to_source(self) -> dict[str, str]:
        payload = asdict(self)
        payload.update({"source_type": "web", "source_ref": f"[WEB:{self.url}]"})
        return payload


class _DuckDuckGoParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.results: list[dict[str, str]] = []
        self._active: dict[str, str] | None = None
        self._capture = ""

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "a" and ("result__a" in classes or "result-link" in classes):
            self._active = {"title": "", "url": _unwrap_url(values.get("href", "")), "snippet": ""}
            self._capture = "title"
        elif self._active is not None and ("result__snippet" in classes or "result-snippet" in classes):
            self._capture = "snippet"

    def handle_data(self, data: str) -> None:
        if self._active is not None and self._capture:
            self._active[self._capture] += data

    def handle_endtag(self, tag: str) -> None:
        if self._active is None:
            return
        if tag == "a" and self._capture == "title":
            self._capture = ""
            if self._active["url"] and self._active["title"].strip():
                self.results.append(self._active)
        elif self._capture == "snippet" and tag in {"a", "div", "td", "span"}:
            self._capture = ""
            if self.results and self.results[-1]["url"] == self._active["url"]:
                self.results[-1]["snippet"] = self._active["snippet"].strip()
            self._active = None


class _TextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str) -> None:
        if data.strip():
            self.parts.append(data.strip())


def _plain_text(value: str) -> str:
    parser = _TextParser()
    parser.feed(unescape(value or ""))
    return " ".join(parser.parts)


def _unwrap_url(value: str) -> str:
    url = unquote(str(value or ""))
    if url.startswith("//"):
        url = "https:" + url
    parsed = urlparse(url)
    if "duckduckgo.com" in parsed.netloc:
        target = parse_qs(parsed.query).get("uddg", [""])[0]
        if target:
            return unquote(target)
    return url


def _default_transport(url: str, timeout: float) -> str:
    request = Request(url, headers={"User-Agent": "Mozilla/5.0 YOLO11DamageDesktop/2.1 read-only-assistant"})
    with urlopen(request, timeout=timeout) as response:  # noqa: S310 - fixed HTTPS search origin
        return response.read(2_000_000).decode("utf-8", errors="replace")


class PublicWebSearchProvider:
    def __init__(self, *, timeout: float = 12.0, transport: Callable[[str, float], str] | None = None) -> None:
        self.timeout = float(timeout)
        self.transport = transport or _default_transport

    def search(self, query: str, *, max_results: int = 5) -> list[WebSearchResult]:
        normalized = " ".join(str(query).split()).strip()
        if not normalized:
            return []
        url = "https://www.bing.com/search?format=rss&q=" + quote_plus(normalized)
        response_text = self.transport(url, self.timeout)
        raw_results: list[dict[str, str]] = []
        try:
            root = ElementTree.fromstring(response_text)
            for item in root.findall(".//item"):
                raw_results.append({
                    "title": str(item.findtext("title") or ""),
                    "url": str(item.findtext("link") or ""),
                    "snippet": _plain_text(str(item.findtext("description") or "")),
                })
        except ElementTree.ParseError:
            pass
        if not raw_results:
            parser = _DuckDuckGoParser()
            parser.feed(response_text)
            raw_results = parser.results
        accessed_at = datetime.now(timezone.utc).isoformat()
        unique: dict[str, WebSearchResult] = {}
        for item in raw_results:
            target = item["url"].strip()
            parsed = urlparse(target)
            if parsed.scheme not in {"http", "https"} or not parsed.netloc:
                continue
            unique[target] = WebSearchResult(item["title"].strip(), target, item["snippet"].strip(), parsed.netloc.casefold(), accessed_at)
            if len(unique) >= max(1, int(max_results)):
                break
        return list(unique.values())


class ResponsesWebSearchProvider:
    """Capability-detected Responses Web Search with verified URL extraction.

    OpenAI-compatible gateways that do not support the Web Search tool raise
    through this adapter; the shared external router then labels the result as
    model prior instead of claiming that a web search happened.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout: float = 60.0,
        client: Any | None = None,
    ) -> None:
        self.api_key = str(api_key or "").strip()
        self.base_url = str(base_url or "").strip()
        self.model = str(model or "").strip()
        self.timeout = float(timeout)
        self.client = client

    @staticmethod
    def _verified_citations(value: Any, *, snippet: str) -> list[WebSearchResult]:
        found: dict[str, WebSearchResult] = {}
        accessed_at = datetime.now(timezone.utc).isoformat()

        def field(item: Any, name: str, default: Any = None) -> Any:
            return item.get(name, default) if isinstance(item, dict) else getattr(item, name, default)

        def walk(item: Any) -> None:
            if item is None or isinstance(item, (str, bytes, int, float, bool)):
                return
            url = str(field(item, "url", "") or "").strip()
            title = str(field(item, "title", "") or field(item, "name", "") or "").strip()
            parsed = urlparse(url)
            if parsed.scheme in {"http", "https"} and parsed.netloc and title:
                found[url] = WebSearchResult(
                    title=title,
                    url=url,
                    snippet=snippet[:800],
                    domain=parsed.netloc.casefold(),
                    accessed_at=accessed_at,
                )
            if isinstance(item, dict):
                children = item.values()
            elif isinstance(item, (list, tuple)):
                children = item
            else:
                dump = getattr(item, "model_dump", None)
                if callable(dump):
                    try:
                        walk(dump())
                    except Exception:
                        pass
                elif hasattr(item, "__dict__"):
                    walk(vars(item))
                return
            for child in children:
                walk(child)

        walk(value)
        return list(found.values())

    def search(self, query: str, *, max_results: int = 5) -> list[WebSearchResult]:
        normalized = " ".join(str(query).split()).strip()
        if not normalized:
            return []
        if not self.api_key or not self.model:
            raise ValueError("responses_web_search_not_configured")
        client = self.client
        if client is None:
            try:
                from openai import OpenAI
                from runtime.responses_damage_report import normalize_responses_base_url
            except ImportError as exc:
                raise RuntimeError("responses_web_search_dependency_missing") from exc
            client = OpenAI(
                api_key=self.api_key,
                base_url=normalize_responses_base_url(self.base_url),
                timeout=self.timeout,
                max_retries=0,
            )
        response = client.responses.create(
            model=self.model,
            input=normalized,
            tools=[{"type": "web_search_preview"}],
        )
        output_text = str(getattr(response, "output_text", "") or "")
        results = self._verified_citations(response, snippet=output_text)
        return results[: max(1, int(max_results))]
