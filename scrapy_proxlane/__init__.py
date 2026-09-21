"""Scrapy downloader middleware for Proxlane.

Routes each request through a Proxlane gateway and hands the gateway's verdict back to the
spider as ``response.meta["proxlane"]``: which provider served it, what the outcome was, what it
cost, and which block-page rule fired if one did.
"""

from scrapy_proxlane.middleware import ProxlaneMiddleware, parse_gateway_headers

__all__ = ["ProxlaneMiddleware", "parse_gateway_headers"]
__version__ = "0.1.0"
