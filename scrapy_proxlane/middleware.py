"""The middleware.

Two jobs, both small. On the way out, rewrite the request so it goes to the gateway's ``/v1``
with the target as the ``url`` parameter and the gateway key in the Authorization header. On the
way back, read the gateway's response headers into ``response.meta["proxlane"]`` and give the
response its original URL back, so the spider never learns a gateway was involved.

Nothing here decides retries or failover. The gateway already tried every provider it could;
by the time a response reaches Scrapy the question is settled, and the headers say how.
"""

from __future__ import annotations

from typing import Any
from urllib.parse import urlencode

from scrapy import Request, signals
from scrapy.exceptions import NotConfigured

# The headers the gateway sets, and the meta key each becomes. Mirrors the response-headers
# table in https://proxlane.dev/docs/api. Anything not listed is left alone.
GATEWAY_HEADERS: dict[bytes, str] = {
    b"X-Outcome": "outcome",
    b"X-Outcome-Class": "outcome_class",
    b"X-Provider-Used": "provider",
    b"X-Attempts": "attempts",
    b"X-Chain": "chain",
    b"X-Cost-Estimate": "cost",
    b"X-Cost-Unit": "cost_unit",
    b"X-Cost-Source": "cost_source",
    b"X-Detect-Rule": "detect_rule",
    b"X-Provider-Health": "provider_health",
    b"X-Ignored-Params": "ignored_params",
    b"X-Request-Id": "request_id",
    b"X-Proxlane-Simulated": "simulated",
}

# Per-request options a spider may put in ``request.meta["proxlane"]``, and the gateway query
# parameter each maps to. Only ``render`` has a default, and it is False: the gateway bills
# rendering at up to ten times a plain fetch, so it is never implied.
REQUEST_PARAMS: dict[str, str] = {
    "country_code": "country_code",
    "provider": "provider",
    "premium": "premium",
    "timeout": "timeout",
    "wait_for": "wait_for",
}

_ROUTED = "_proxlane_routed"
_ORIGINAL_URL = "_proxlane_original_url"


def parse_gateway_headers(headers: Any) -> dict[str, Any]:
    """Read the gateway's headers into a plain dict.

    ``attempts`` becomes an int; everything else stays a string, including ``cost``, because the
    gateway writes ``mixed`` there when a chain spent in two units and a float would turn that
    into NaN.
    """
    out: dict[str, Any] = {}
    for name, key in GATEWAY_HEADERS.items():
        value = headers.get(name)
        if value is None:
            continue
        text = value.decode("utf-8") if isinstance(value, bytes) else str(value)
        out[key] = int(text) if key == "attempts" and text.isdigit() else text
    return out


class ProxlaneMiddleware:
    """Send every request through a Proxlane gateway.

    Settings:

    ``PROXLANE_URL``
        The gateway, e.g. ``http://localhost:8787``. Required.
    ``PROXLANE_API_KEY``
        The gateway's own key (``PROXLANE_API_KEY`` on the gateway side). Required.
    ``PROXLANE_ENABLED``
        Default True. False disables the middleware without removing it from the settings.
    ``PROXLANE_DEFAULT_RENDER``
        Default False. Whether requests render JavaScript unless they say otherwise.

    Per request, ``request.meta["proxlane"]`` may be ``False`` to bypass the gateway, or a dict
    with any of ``render``, ``binary``, ``country_code``, ``provider``, ``premium``, ``timeout``,
    ``wait_for`` and ``simulate``. ``simulate`` sets ``X-Proxlane-Simulate`` and only means
    anything with a gateway sandbox key; with a live key the gateway refuses it with a 400,
    which is the gateway's protection against spending real credits on a test.
    """

    def __init__(
        self,
        gateway_url: str,
        api_key: str,
        *,
        default_render: bool = False,
        stats: Any = None,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.api_key = api_key
        self.default_render = default_render
        self.stats = stats

    @classmethod
    def from_crawler(cls, crawler: Any) -> ProxlaneMiddleware:
        s = crawler.settings
        if not s.getbool("PROXLANE_ENABLED", True):
            raise NotConfigured("PROXLANE_ENABLED is False")
        url = s.get("PROXLANE_URL")
        key = s.get("PROXLANE_API_KEY")
        missing = [n for n, v in (("PROXLANE_URL", url), ("PROXLANE_API_KEY", key)) if not v]
        if missing:
            raise NotConfigured(f"scrapy-proxlane needs {', '.join(missing)} in settings")
        mw = cls(
            url,
            key,
            default_render=s.getbool("PROXLANE_DEFAULT_RENDER", False),
            stats=crawler.stats,
        )
        crawler.signals.connect(mw.spider_opened, signal=signals.spider_opened)
        return mw

    def spider_opened(self, spider: Any) -> None:
        spider.logger.info("scrapy-proxlane: routing through %s", self.gateway_url)

    def gateway_request(self, request: Request) -> Request | None:
        """The request as it will reach the gateway, or None if this one bypasses it."""
        opts = request.meta.get("proxlane")
        if opts is False or request.meta.get(_ROUTED):
            return None
        opts = dict(opts or {})

        params: dict[str, str] = {"url": request.url}
        # Explicit on every request. The gateway defaults render to false as well, but a
        # default that lives in two places is a default that can disagree.
        render = bool(opts.get("render", self.default_render))
        params["render"] = "true" if render else "false"
        if opts.get("binary"):
            params["binary"] = "true"
        for opt, param in REQUEST_PARAMS.items():
            if opt in opts and opts[opt] is not None:
                params[param] = str(opts[opt])

        headers = request.headers.copy()
        headers[b"Authorization"] = f"Bearer {self.api_key}".encode()
        if opts.get("simulate"):
            headers[b"X-Proxlane-Simulate"] = str(opts["simulate"]).encode()

        meta = dict(request.meta)
        meta[_ROUTED] = True
        meta[_ORIGINAL_URL] = request.url
        # The gateway URL differs per target, so the dupefilter still sees one URL per page.
        return request.replace(
            url=f"{self.gateway_url}/v1?{urlencode(params)}",
            headers=headers,
            meta=meta,
        )

    def process_request(self, request: Request, spider: Any) -> Request | None:
        return self.gateway_request(request)

    def process_response(self, request: Request, response: Any, spider: Any) -> Any:
        if not request.meta.get(_ROUTED):
            return response
        info = parse_gateway_headers(response.headers)
        request.meta["proxlane"] = info
        if self.stats is not None:
            self.stats.inc_value("proxlane/requests")
            if "outcome" in info:
                self.stats.inc_value(f"proxlane/outcome/{info['outcome']}")
            if "provider" in info:
                self.stats.inc_value(f"proxlane/provider/{info['provider']}")
            if "attempts" in info:
                self.stats.inc_value("proxlane/attempts", info["attempts"])
        # The spider asked for the target, so it gets a response whose URL is the target.
        # Relative links then resolve against the page rather than the gateway.
        return response.replace(url=request.meta[_ORIGINAL_URL])
