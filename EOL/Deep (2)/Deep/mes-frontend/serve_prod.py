#!/usr/bin/env python3
"""
Production static server + reverse proxy for the MES frontend.

Replaces `python -m http.server` on port 5656.  The Windows setup used
Caddy to serve mes-frontend/dist AND reverse-proxy the API; a plain
http.server can't proxy, so login (POST /api/auth/login) returned 501.

Routing (mirrors mes-frontend/vite.config.js):
    /api/*      -> http://127.0.0.1:8080         (MES-API, path kept)
    /cms-api/*  -> http://127.0.0.1:5555         (CMS-API, /cms-api stripped)
    everything else -> static files from ./dist
                       (SPA fallback to index.html for client routes)

stdlib only.  Usage:  python3 serve_prod.py [port] [dist_dir]
"""
import os, sys, urllib.request, urllib.error
import datetime as _dt
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from functools import partial

PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 5656
DIST = os.path.abspath(sys.argv[2]) if len(sys.argv) > 2 else \
       os.path.join(os.path.dirname(os.path.abspath(__file__)), "dist")

ROUTES = [
    ("/api",     "http://127.0.0.1:8080", False),  # keep path
    ("/cms-api", "http://127.0.0.1:5555", True),   # strip prefix
]

# Hop-by-hop headers must not be forwarded (RFC 7230 §6.1).
HOP = {"connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
       "te", "trailers", "transfer-encoding", "upgrade", "content-length", "host"}

# Port the 15-user emulator grid is served on (loadtest/usergrid.py).
EMULATOR_GRID_PORT = 8095

MIME = {".html":"text/html",".js":"text/javascript",".mjs":"text/javascript",
        ".css":"text/css",".json":"application/json",".png":"image/png",
        ".jpg":"image/jpeg",".jpeg":"image/jpeg",".svg":"image/svg+xml",
        ".ico":"image/x-icon",".woff":"font/woff",".woff2":"font/woff2",
        ".map":"application/json",".webp":"image/webp",".gif":"image/gif"}


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    # 2026-08-20 — TEMPORARY request log, switched on to chase one wall panel
    # that keeps painting "Offline".  Set MES_ACCESS_LOG to a client IP (or
    # "all") to see that client's requests; unset = silent, exactly as before.
    _ACCESS_LOG = os.environ.get("MES_ACCESS_LOG", "").strip()

    def log_message(self, fmt, *a):
        want = self._ACCESS_LOG
        if not want:
            return
        ip = self.client_address[0] if self.client_address else "?"
        if want != "all" and ip != want:
            return
        try:
            sys.stderr.write("[ACC] %s %-15s %s\n" % (
                _dt.datetime.now().strftime("%H:%M:%S.%f")[:12], ip, fmt % a))
            sys.stderr.flush()
        except Exception:
            pass

    def _match(self):
        for prefix, target, strip in ROUTES:
            if self.path == prefix or self.path.startswith(prefix + "/"):
                return prefix, target, strip
        return None

    def _read_body(self):
        # 2026-07-30 — handle CHUNKED request bodies.  cloudflared sends some
        # POSTs with `Transfer-Encoding: chunked` (no Content-Length).  The old
        # code read Content-Length ONLY, so a chunked body was left UNREAD in the
        # socket; with HTTP/1.1 keep-alive the NEXT request then read the leftover
        # chunk terminator "0" -> "Bad request syntax ('0')" 400 spam in the
        # tunnel log.  De-chunk here + forward a normal Content-Length'd body.
        te = (self.headers.get("Transfer-Encoding", "") or "").lower()
        if "chunked" in te:
            parts = []
            while True:
                line = self.rfile.readline()
                if not line:
                    break
                line = line.strip()
                if not line:
                    continue
                try:
                    size = int(line.split(b";", 1)[0], 16)
                except ValueError:
                    break
                if size == 0:
                    self.rfile.readline()   # consume the trailing CRLF
                    break
                parts.append(self.rfile.read(size))
                self.rfile.readline()        # CRLF after each chunk
            return b"".join(parts) or None
        length = int(self.headers.get("Content-Length", 0) or 0)
        return self.rfile.read(length) if length else None

    def _proxy(self, prefix, target, strip):
        upstream_path = self.path[len(prefix):] if strip else self.path
        if strip and not upstream_path.startswith("/"):
            upstream_path = "/" + upstream_path
        url = target + upstream_path
        body = self._read_body()
        req = urllib.request.Request(url, data=body, method=self.command)
        for k, v in self.headers.items():
            if k.lower() not in HOP and k.lower() != "x-forwarded-for":
                req.add_header(k, v)         # Authorization (JWT) + Range preserved here
        # 2026-09-13 — forward the real panel/client IP so MES-API can map a
        # panel (by IP) to its auto-open dashboard (see routers/panel.py).
        try:
            _cip = self.client_address[0] if self.client_address else ""
            _xff = self.headers.get("X-Forwarded-For")
            req.add_header("X-Forwarded-For", f"{_xff}, {_cip}" if _xff else _cip)
        except Exception:
            pass
        try:
            up = urllib.request.urlopen(req, timeout=120)
        except urllib.error.HTTPError as e:  # 4xx/5xx still carry a body (e.g. 401 login)
            up = e
        except Exception as e:
            self.send_error(502, f"upstream error: {e}")
            return
        try:
            self._stream(getattr(up, "status", None) or up.code, up.headers, up)
        finally:
            try: up.close()
            except Exception: pass

    def _stream(self, status, headers, up):
        # 2026-07-31 — STREAM the upstream body instead of buffering it whole
        # (`up.read()`).  A cycle-video MP4 was fully downloaded into RAM here
        # before a single byte reached the browser -> the operator's "video
        # bhot time leti / latency" through the Cloudflare tunnel.  Now we
        # forward the real upstream Content-Length (so the browser gets a
        # seekable, progress-bar'd stream that starts playing on the first
        # faststart bytes) and copy the body in 64 KB chunks.
        self.send_response(status)
        has_len = False
        for k, v in headers.items():
            lk = k.lower()
            if lk in ("connection", "keep-alive", "transfer-encoding",
                      "upgrade", "host"):
                continue
            if lk == "content-length":
                has_len = True
            self.send_header(k, v)
        if not has_len:
            # no length -> must delimit the body by closing the connection
            self.send_header("Connection", "close")
            self.close_connection = True
        self.end_headers()
        if self.command == "HEAD":
            return
        while True:
            try:
                chunk = up.read(65536)
            except Exception:
                break
            if not chunk:
                break
            try:
                self.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError, OSError):
                break   # browser closed the video early — normal, stop quietly

    def _static(self):
        # strip query, normalise, prevent path traversal
        path = self.path.split("?", 1)[0].split("#", 1)[0]
        rel = path.lstrip("/") or "index.html"
        full = os.path.normpath(os.path.join(DIST, rel))
        if not full.startswith(DIST):
            self.send_error(403); return
        if not os.path.isfile(full):
            full = os.path.join(DIST, "index.html")   # SPA fallback (/login, /dashboard, refresh)
        try:
            with open(full, "rb") as f:
                data = f.read()
        except OSError:
            self.send_error(404); return
        ext = os.path.splitext(full)[1].lower()
        total = len(data)

        # 2026-09-13 — Byte-RANGE support (RFC 7233).  Android's system
        # DownloadManager (used by the browser when it saves the .apk) probes
        # with `Range:` headers and can hang/retry — or corrupt the file — if the
        # server ignores the range or answers it wrongly.  serve_prod used to
        # answer every static request with a plain `200` and no `Accept-Ranges`.
        # Handle it correctly: 200 for the whole file (incl. `bytes=0-`), 206 for
        # a TRUE sub-range, 416 for an unsatisfiable range — and always advertise
        # Accept-Ranges.  (An earlier attempt returned the FULL body for an
        # out-of-bounds range instead of 416, which fed the download manager
        # extra bytes and left the download stuck ".pending"/spinning.)
        rng = (self.headers.get("Range") or "").strip()
        start, end, status = 0, total - 1, 200
        if rng.lower().startswith("bytes=") and self.command in ("GET", "HEAD") and total > 0:
            try:
                spec = rng.split("=", 1)[1].split(",")[0].strip()
                s, _, e = spec.partition("-")
                if s == "":                                  # suffix: bytes=-N (last N)
                    n = int(e)
                    if n > 0:
                        start, end, status = max(0, total - n), total - 1, 206
                else:
                    start = int(s)
                    end = int(e) if e else total - 1
                    if start >= total or start > end:
                        status = 416                         # unsatisfiable
                    else:
                        if end >= total:
                            end = total - 1
                        if start > 0 or end < total - 1:     # a real sub-range
                            status = 206
                        # else the range covers the whole file → keep 200
            except Exception:
                start, end, status = 0, total - 1, 200

        if status == 416:
            self.send_response(416)
            self.send_header("Content-Range", f"bytes */{total}")
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        body = data[start:end + 1] if status == 206 else data
        self.send_response(status)
        if ext == ".apk":
            # 2026-09-05 — Serve the Android app package as a real download. The
            # correct MIME lets Android recognise it as installable, and
            # Content-Disposition:attachment makes the browser download it
            # immediately instead of opening a BLANK tab trying to render the
            # octet-stream (which looked broken — "kuch download nahi hota").
            # The in-app "Update now" hands this URL to the system browser.
            self.send_header("Content-Type", "application/vnd.android.package-archive")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{os.path.basename(full)}"')
        else:
            self.send_header("Content-Type", MIME.get(ext, "application/octet-stream"))
        self.send_header("Accept-Ranges", "bytes")
        if status == 206:
            self.send_header("Content-Range", f"bytes {start}-{end}/{total}")
        self.send_header("Content-Length", str(len(body)))
        # basic security headers (Caddy added these on Windows)
        self.send_header("X-Content-Type-Options", "nosniff")
        # 2026-09-17 — SAMEORIGIN stays the rule for everyone.  The single
        # exception is a client connecting from LOOPBACK, which is only ever
        # this machine: loadtest/usergrid.html shows 15 users side by side, and
        # each pane has to be a different ORIGIN (127.0.0.1 … 127.0.0.15) or
        # they would share one sessionStorage and all be the same user.
        # SAMEORIGIN blocks that, so loopback callers get a frame-ancestors
        # allowance naming ONLY the emulator page instead.  A LAN or tunnel
        # client never matches this test and sees exactly the old header, so
        # the clickjacking protection that matters is untouched.
        _cip = self.client_address[0] if self.client_address else ""
        if _cip.startswith("127.") or _cip == "::1":
            self.send_header(
                "Content-Security-Policy",
                "frame-ancestors 'self' "
                + " ".join(f"http://127.0.0.{n}:{EMULATOR_GRID_PORT}"
                           for n in range(1, 16)))
        else:
            self.send_header("X-Frame-Options", "SAMEORIGIN")
        # ── Cache policy (2026-08-14) ────────────────────────────────────
        # Nothing here sent ANY cache header before, so browsers fell back to
        # heuristic caching and held on to index.html indefinitely.  Because
        # index.html is what names the hashed bundle, a user could keep loading
        # a months-old app no matter how many times the dist was rebuilt — the
        # deploy looked done from the server side and changed nothing on screen.
        #
        # The split is the standard one for a hashed SPA build:
        #   /assets/*  — the filename contains a content hash, so a changed file
        #                is a NEW url.  Safe to cache forever.
        #   index.html — the pointer to those names.  Must never be cached, or
        #                the pointer goes stale and the new bundles are unreachable.
        is_html = ext in (".html", ".htm")
        if is_html:
            self.send_header("Cache-Control", "no-store, must-revalidate")
            self.send_header("Pragma", "no-cache")
            self.send_header("Expires", "0")
        elif "/assets/" in path:
            self.send_header("Cache-Control", "public, max-age=31536000, immutable")
        else:
            # icons, manifest and friends: revalidate, but allow reuse.
            self.send_header("Cache-Control", "public, max-age=3600")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _dispatch(self):
        m = self._match()
        if m:
            self._proxy(*m)
        elif self.command in ("GET", "HEAD"):
            self._static()
        else:
            self.send_error(405)

    do_GET = do_POST = do_PUT = do_DELETE = do_PATCH = do_HEAD = _dispatch


if __name__ == "__main__":
    if not os.path.isdir(DIST):
        sys.exit(f"[FATAL] dist dir not found: {DIST}")
    # 2026-08-20 — LAN TV panels were flipping to "Offline" every few minutes
    # while the browser ON the server never did.  Cause was the accept queue,
    # not the app: socketserver defaults request_queue_size to 5, and the
    # kernel had already logged 1116 "listen queue overflowed / SYNs dropped".
    # Seven wall panels each hold 6-9 keep-alive connections, so whenever a
    # few re-dial at once (page reload, keep-alive expiry, a switch blip) the
    # backlog of 5 fills and the kernel silently drops the SYN -- the panel's
    # 3 s poll fails and the dashboard paints "Offline".  Localhost never felt
    # it because those sockets were already established.
    # 128 is the usual production value and costs nothing but kernel memory.
    ThreadingHTTPServer.request_queue_size = 128
    srv = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"MES frontend + API proxy on :{PORT}  (dist={DIST})", flush=True)
    print("  /api -> :8080   /cms-api -> :5555   else -> static", flush=True)
    srv.serve_forever()
