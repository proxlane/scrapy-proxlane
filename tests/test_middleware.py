"""Unit tests: the rewrite on the way out and the header read on the way back.

Nothing here talks to a gateway. ``test_gateway.py`` does, against a real one in sandbox mode.
"""

from urllib.parse import parse_qs, urlsplit

import pytest
from scrapy import Request
from scrapy.exceptions import NotConfigured
from scrapy.http import HtmlResponse
from scrapy.utils.test import get_crawler

from scrapy_proxlane import ProxlaneMiddleware, parse_gateway_headers

GATEWAY = "http://gateway.test:8787"
KEY = "k" * 32


def mw(**settings):
    crawler = get_crawler(
        settings_dict={"PROXLANE_URL": GATEWAY, "PROXLANE_API_KEY": KEY, **settings}
    )
    return ProxlaneMiddleware.from_crawler(crawler)


def query(request: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlsplit(request.url).query).items()}


def test_rewrites_to_the_gateway_with_the_target_as_url():
    out = mw().gateway_request(Request("https://example.com/a?b=1"))
    assert out is not None
    assert out.url.startswith(f"{GATEWAY}/v1?")
    assert query(out) == {"url": "https://example.com/a?b=1", "render": "false"}
    assert out.headers[b"Authorization"] == f"Bearer {KEY}".encode()


def test_render_is_explicit_and_defaults_off():
    assert query(mw().gateway_request(Request("https://example.com/")))["render"] == "false"
    on = mw(PROXLANE_DEFAULT_RENDER=True).gateway_request(Request("https://example.com/"))
    assert query(on)["render"] == "true"
    off = mw(PROXLANE_DEFAULT_RENDER=True).gateway_request(
        Request("https://example.com/", meta={"proxlane": {"render": False}})
    )
    assert query(off)["render"] == "false"


def test_per_request_options_become_gateway_parameters():
    req = Request(
        "https://example.com/",
        meta={
            "proxlane": {
                "render": True,
                "binary": True,
                "country_code": "de",
                "provider": "scrapfly",
                "premium": "residential",
                "timeout": 30000,
                "wait_for": "#main",
                "simulate": "SOFT_BLOCK",
            }
        },
    )
    out = mw().gateway_request(req)
    assert query(out) == {
        "url": "https://example.com/",
        "render": "true",
        "binary": "true",
        "country_code": "de",
        "provider": "scrapfly",
        "premium": "residential",
        "timeout": "30000",
        "wait_for": "#main",
    }
    assert out.headers[b"X-Proxlane-Simulate"] == b"SOFT_BLOCK"


def test_bypass_and_no_double_routing():
    assert mw().gateway_request(Request("https://example.com/", meta={"proxlane": False})) is None
    once = mw().gateway_request(Request("https://example.com/"))
    # A retry re-enters process_request with the rewritten request. It must not be wrapped again.
    assert mw().gateway_request(once) is None


def test_post_body_and_headers_survive():
    req = Request(
        "https://example.com/form",
        method="POST",
        body=b"a=1",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    out = mw().gateway_request(req)
    assert out.method == "POST"
    assert out.body == b"a=1"
    assert out.headers[b"Content-Type"] == b"application/x-www-form-urlencoded"


def test_response_gets_its_url_back_and_the_verdict_in_meta():
    m = mw()
    routed = m.gateway_request(Request("https://example.com/page"))
    resp = HtmlResponse(
        routed.url,
        status=200,
        headers={
            "X-Outcome": "OK",
            "X-Outcome-Class": "ok",
            "X-Provider-Used": "scrapfly",
            "X-Attempts": "2",
            "X-Chain": "scraperapi:PROVIDER_TIMEOUT>scrapfly:OK",
            "X-Cost-Estimate": "mixed",
            "X-Request-Id": "01ABC",
        },
        body=b"<html><a href='/next'>n</a></html>",
        request=routed,
    )
    out = m.process_response(routed, resp, spider=None)
    assert out.url == "https://example.com/page"
    assert out.meta["proxlane"] == {
        "outcome": "OK",
        "outcome_class": "ok",
        "provider": "scrapfly",
        "attempts": 2,
        "chain": "scraperapi:PROVIDER_TIMEOUT>scrapfly:OK",
        "cost": "mixed",
        "request_id": "01ABC",
    }
    # Relative links resolve against the page, not the gateway.
    assert out.urljoin("/next") == "https://example.com/next"
    assert m.stats.get_value("proxlane/outcome/OK") == 1
    assert m.stats.get_value("proxlane/provider/scrapfly") == 1
    assert m.stats.get_value("proxlane/attempts") == 2


def test_unrouted_response_is_untouched():
    m = mw()
    req = Request("https://example.com/", meta={"proxlane": False})
    resp = HtmlResponse(req.url, body=b"", request=req)
    assert m.process_response(req, resp, spider=None) is resp


def test_parse_headers_keeps_cost_as_text():
    assert parse_gateway_headers({b"X-Cost-Estimate": b"1.000000", b"X-Attempts": b"x"}) == {
        "cost": "1.000000",
        "attempts": "x",
    }


def test_refuses_to_start_half_configured():
    with pytest.raises(NotConfigured, match="PROXLANE_API_KEY"):
        ProxlaneMiddleware.from_crawler(get_crawler(settings_dict={"PROXLANE_URL": GATEWAY}))
    with pytest.raises(NotConfigured, match="PROXLANE_ENABLED"):
        ProxlaneMiddleware.from_crawler(
            get_crawler(
                settings_dict={
                    "PROXLANE_URL": GATEWAY,
                    "PROXLANE_API_KEY": KEY,
                    "PROXLANE_ENABLED": False,
                }
            )
        )
