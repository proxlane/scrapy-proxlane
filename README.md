# scrapy-proxlane

A Scrapy downloader middleware that sends every request through a [Proxlane](https://github.com/proxlane/proxlane) gateway.

Proxlane is a self-hosted gateway in front of the scraping APIs you already pay for: ScraperAPI, ScrapingBee, Scrapfly, Bright Data and Firecrawl. One endpoint. When a provider blocks, errors or times out it fails over to the next, and every page is checked for a captcha before it counts as a success. This middleware puts that behind your spiders, and hands each verdict back in `response.meta`.

## Install

```bash
pip install scrapy-proxlane
```

You need a running gateway. One container, your own provider keys:

```bash
docker run -p 8787:8787 \
  -e PROXLANE_API_KEY="$(openssl rand -hex 32)" \
  -e SCRAPERAPI_KEY=... \
  ghcr.io/proxlane/gateway:latest
```

## Configure

```python
# settings.py
DOWNLOADER_MIDDLEWARES = {
    "scrapy_proxlane.ProxlaneMiddleware": 585,
}
PROXLANE_URL = "http://localhost:8787"
PROXLANE_API_KEY = "..."  # the gateway's key, not a provider's
PROXLANE_DEFAULT_RENDER = False  # rendering costs up to 10x; opt in per request
```

Priority 585 puts it after RetryMiddleware (550), so a retry re-enters the middleware, and before HttpProxyMiddleware (750).

## Use

Nothing changes in the spider. Requests go to the gateway, responses come back with the target's URL, so relative links resolve as before.

```python
def parse(self, response):
    info = response.meta["proxlane"]
    # {'outcome': 'OK', 'outcome_class': 'ok', 'provider': 'scrapfly', 'attempts': 2,
    #  'chain': 'scraperapi:PROVIDER_TIMEOUT>scrapfly:OK', 'cost': '6.000000', ...}
    if info["outcome_class"] == "blocked":
        return
```

Per-request options go in `request.meta["proxlane"]`:

```python
yield scrapy.Request(
    url,
    meta={
        "proxlane": {
            "render": True,  # JavaScript rendering
            "country_code": "de",  # geotargeting
            "provider": "scrapfly",  # pin one provider, no failover
            "premium": "residential",  # none | residential | stealth
            "timeout": 30000,  # per-request deadline, ms
            "wait_for": "#results",  # CSS selector the renderer waits for
            "binary": True,  # bytes intact, for images and PDFs
        }
    },
)
```

`meta={"proxlane": False}` sends a request directly, bypassing the gateway.

## What comes back

`response.meta["proxlane"]` holds the gateway's headers as a dict: `outcome`, `outcome_class`, `provider`, `attempts`, `chain`, `cost`, `cost_unit`, `cost_source`, `detect_rule`, `provider_health`, `ignored_params`, `request_id`. The full meaning of each is in the [API reference](https://proxlane.dev/docs/api) and the [outcomes page](https://proxlane.dev/docs/outcomes).

Branch on `outcome_class`, which is a closed set: `ok`, `blocked`, `target`, `provider`, `client`, `gateway`. `outcome` gains members as adapters land.

Scrapy stats: `proxlane/requests`, `proxlane/attempts`, `proxlane/outcome/<OUTCOME>`, `proxlane/provider/<id>`.

## Status codes and retries

The gateway returns the target's status for `ok` and `target` outcomes, 502 for `blocked` and `provider`, 503 when no provider could serve, 504 on a deadline. Scrapy's RetryMiddleware retries 502, 503 and 504 by default. The gateway has already tried every provider it could by then, so set `RETRY_TIMES` low or drop those codes from `RETRY_HTTP_CODES` if you'd rather not pay for a second walk of the chain.

## Testing without provider keys

Run the gateway with `PROXLANE_SANDBOX_KEY` set and use that key as `PROXLANE_API_KEY` in your test settings. Then `meta={"proxlane": {"simulate": "SOFT_BLOCK"}}` returns exactly what a blocked page returns, real headers included, without calling any provider or spending a credit. With a live key the gateway refuses the simulate header with a 400, so a test can never spend by accident.

This package's own integration tests run that way; see `tests/test_gateway.py`.

## Licence

Apache-2.0.
