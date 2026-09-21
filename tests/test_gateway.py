"""Against a real gateway in sandbox mode. Nothing is mocked and no provider is called.

The gateway's sandbox key answers ``/v1`` from its own outcome table, with the real headers, and
never reaches a provider. So this runs in CI with no provider keys and spends nothing, and what
it checks is the real thing: the bytes a gateway sends, read by this middleware.

Set ``PROXLANE_TEST_URL`` and ``PROXLANE_TEST_SANDBOX_KEY`` to run it; skipped otherwise.
"""

import os
import urllib.request

import pytest
from scrapy import Request
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from scrapy_proxlane import ProxlaneMiddleware

URL = os.environ.get("PROXLANE_TEST_URL")
SANDBOX_KEY = os.environ.get("PROXLANE_TEST_SANDBOX_KEY")

pytestmark = pytest.mark.skipif(
    not (URL and SANDBOX_KEY), reason="PROXLANE_TEST_URL and PROXLANE_TEST_SANDBOX_KEY unset"
)


def fetch(request: Request) -> HtmlResponse:
    """Execute a Scrapy request with urllib, so no reactor is needed, and rebuild a Response."""
    req = urllib.request.Request(
        request.url,
        method=request.method,
        data=request.body or None,
        headers={k.decode(): v[0].decode() for k, v in request.headers.items()},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            status, headers, body = r.status, dict(r.headers.items()), r.read()
    except urllib.error.HTTPError as e:
        status, headers, body = e.code, dict(e.headers.items()), e.read()
    return HtmlResponse(request.url, status=status, headers=headers, body=body, request=request)


def middleware() -> ProxlaneMiddleware:
    crawler = get_crawler(settings_dict={"PROXLANE_URL": URL, "PROXLANE_API_KEY": SANDBOX_KEY})
    return ProxlaneMiddleware.from_crawler(crawler)


def roundtrip(meta: dict) -> HtmlResponse:
    m = middleware()
    routed = m.gateway_request(Request("https://example.com/page", meta=meta))
    return m.process_response(routed, fetch(routed), spider=None)


def test_simulated_ok_is_a_real_200_with_the_gateway_headers():
    out = roundtrip({"proxlane": {"simulate": "OK"}})
    assert out.status == 200
    assert out.url == "https://example.com/page"
    info = out.meta["proxlane"]
    assert info["outcome"] == "OK"
    assert info["outcome_class"] == "ok"
    assert info["simulated"] == "OK"
    assert info["attempts"] == 1
    assert "request_id" in info
    assert b"Simulated OK" in out.body


def test_simulated_block_carries_class_rule_and_chain():
    out = roundtrip({"proxlane": {"simulate": "SOFT_BLOCK"}})
    assert out.status == 502
    info = out.meta["proxlane"]
    assert info["outcome"] == "SOFT_BLOCK"
    assert info["outcome_class"] == "blocked"
    assert info["detect_rule"]
    assert ":SOFT_BLOCK" in info["chain"]


def test_the_edge_guard_still_applies_through_the_middleware():
    m = middleware()
    routed = m.gateway_request(
        Request("http://169.254.169.254/latest/meta-data/", meta={"proxlane": {"simulate": "OK"}})
    )
    out = m.process_response(routed, fetch(routed), spider=None)
    assert out.status == 403
    assert out.meta["proxlane"]["outcome"] == "TARGET_FORBIDDEN"
