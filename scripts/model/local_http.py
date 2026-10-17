"""Model verification stays on literal loopback, bypassing proxies and redirects."""

import ipaddress
import urllib.error
import urllib.request
from urllib.parse import urlsplit


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise urllib.error.HTTPError(req.full_url, code, "Local redirect refused", headers, fp)


def open_local(request, *, timeout):
    url = request.full_url if isinstance(request, urllib.request.Request) else request
    parsed = urlsplit(url)
    if (
        parsed.scheme != "http"
        or parsed.username
        or parsed.password
        or not ipaddress.ip_address(parsed.hostname or "").is_loopback
    ):
        raise ValueError("Model verification only permits literal loopback HTTP")
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    return opener.open(request, timeout=timeout)
