"""
security_headers.py
===================
Defense-in-depth HTTP security headers for the MES API (finding: Headers).

A thin ASGI middleware that appends a fixed set of security headers to every
response.  It does NOT use Starlette's BaseHTTPMiddleware (which wraps each
request in an extra async task) — on a single-worker box that overhead is not
free, so this just mutates the outgoing header list and gets out of the way.

IMPORTANT — these are the APP-level, defense-in-depth copy.  The MES HTML
document itself is served at the edge (Caddy / Cloudflare), so the headers that
only matter on the DOCUMENT response — the real Content-Security-Policy and
Strict-Transport-Security with preload — MUST ALSO be enforced there, together
with "Always Use HTTPS" (HTTP->HTTPS redirect).  See the edge checklist at the
bottom of this file.

CSP is shipped in REPORT-ONLY mode first (`Content-Security-Policy-Report-Only`)
so it observes and reports violations without breaking anything.  Once the
report stream is clean, promote it to the enforcing `Content-Security-Policy`
header (ideally at the edge, on the HTML response).
"""
from __future__ import annotations

# style-src keeps 'unsafe-inline' + Google Fonts because the current build uses
# inline styles and the Fonts stylesheet; font-src allows gstatic for the same.
# img-src allows data: and blob: for inline icons and the video/clip blobs.
_CSP = (
    "default-src 'self'; "
    "script-src 'self'; "
    "style-src 'self' https://fonts.googleapis.com 'unsafe-inline'; "
    "font-src 'self' https://fonts.gstatic.com; "
    "img-src 'self' data: blob:; "
    "connect-src 'self'; "
    "frame-ancestors 'self'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "object-src 'none'"
)

# (name, value) pairs, pre-encoded to bytes for the ASGI header list.
_SECURITY_HEADERS = [
    (b"strict-transport-security", b"max-age=63072000; includeSubDomains"),
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"strict-origin-when-cross-origin"),
    (b"permissions-policy",
     b"camera=(), microphone=(), geolocation=(), payment=(), usb=()"),
    # Report-only first — observe, don't block.  Promote to
    # `content-security-policy` once the report stream is clean.
    (b"content-security-policy-report-only", _CSP.encode("latin-1")),
]

_MANAGED = {name for name, _ in _SECURITY_HEADERS}


class SecurityHeadersMiddleware:
    """Append security headers to every HTTP response."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_wrapper(message):
            if message["type"] == "http.response.start":
                headers = message.setdefault("headers", [])
                # Drop any pre-existing copy (idempotent), then set ours.
                headers[:] = [(k, v) for (k, v) in headers if k.lower() not in _MANAGED]
                headers.extend(_SECURITY_HEADERS)
            await send(message)

        await self.app(scope, receive, send_wrapper)


# ── Edge (Caddy / Cloudflare) checklist — enforce ALSO at the edge ───────────
# [ ] Always Use HTTPS (HTTP -> HTTPS redirect).
# [ ] Strict-Transport-Security on the HTML doc (add `preload` once verified).
# [ ] Content-Security-Policy on the HTML doc — start as -Report-Only, then
#     promote to enforcing once the report stream is clean.
# [ ] frame-ancestors: drop any dev entries (e.g. 127.0.0.1:8095) unless the
#     Android TWA actually needs them.
