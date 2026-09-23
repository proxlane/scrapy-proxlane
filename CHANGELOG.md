# Changelog

## 0.1.0

First release.

- `ProxlaneMiddleware`, a Scrapy downloader middleware that sends each request through a Proxlane gateway and returns the target's own URL, so relative links resolve unchanged.
- Per-request options in `request.meta["proxlane"]`: `render`, `country_code`, `provider`, `premium`, `timeout`, `wait_for`, `binary`, and `simulate` for sandbox tests. `meta={"proxlane": False}` bypasses the gateway.
- The gateway's verdict in `response.meta["proxlane"]`: outcome, outcome class, provider, attempts, the failover chain, cost, which block-page rule fired, and the request id.
- Scrapy stats for requests, attempts, outcomes and providers.
- Settings: `PROXLANE_URL`, `PROXLANE_API_KEY`, `PROXLANE_ENABLED`, `PROXLANE_DEFAULT_RENDER`.
