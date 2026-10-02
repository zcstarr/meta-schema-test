#!/usr/bin/env python3
"""Tiny static + redirect simulator. Zero deps, stdlib only.

Usage:
  python serve.py [--port 8000] [--host 0.0.0.0] [--dir .] [--config routes.json]
                  [--versions v1.3,v1.4]

  Binds 0.0.0.0 by default, CORS * open, serves statics + routes from "/" root.
  No prefix required — sim endpoints live at root.

  Versioned test harness layout (straightforward clean pattern):
    ./v1.3/...   -> http://host:port/v1.3/...
    ./v1.4/...   -> http://host:port/v1.4/...
    ./...        -> http://host:port/...      (root, shared)
    /            -> harness index (lists versions, or ./index.html if present)

  Clean URLs: extensionless resolve, so /v1.3/schema hits
  ./v1.3/schema.html (or .md/.json if that's what exists).

Config (routes.json):
[
  {"path": "/old", "status": 301, "location": "/new"},
  {"path": "/v1.3/old", "status": 301, "location": "/v1.3/new"},
  {"version": "v1.3", "path": "/old", "status": 301, "location": "/new"},
  {"version": ["v1.3", "v1.4"], "path": "/old", "status": 301, "location": "/new"},
  {"method": "GET", "path": "/api/user", "status": 200,
   "headers": {"Content-Type": "application/json"}, "body": "{\"ok\": true}"},
  {"path": "/slow", "status": 200, "body": "slow page", "delay": 2.5},
  {"path": "/blog/*", "status": 302, "location": "/new-blog/*"},
  {"path": "/meta-refresh", "meta_refresh": {"to": "/target", "seconds": 2}}
]
  "version" is shorthand: expands path+location to /<version><path>.
  Use it to keep v1.3 / v1.4 cases DRY. Omit for unversioned/shared routes.
]

Built-ins (no config needed, httpbin-lite) — served from "/" root, no prefix.
Both /status/404 and /__status/404 work (legacy __ alias kept):
  /status/404                -> returns any status code
  /redirect?to=/foo&code=301 -> redirect (code 301,302,303,307,308)
  /delay/2?then=/foo         -> sleep 2s then 200 (or redirect if ?then=)
  /echo                      -> dumps method/path/headers/body as JSON
  /routes                    -> lists active routes
"""
import argparse
import fnmatch
import json
import mimetypes
import os
import time
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class SimHandler(BaseHTTPRequestHandler):
    routes = []
    serve_dir = "."
    versions = ["v1.3", "v1.4"]
    server_version = "SimServer/1.0"

    def log_message(self, fmt, *args):
        # terse + useful: METHOD path -> status
        print(f"{self.command} {self.path} - {fmt % args}")

    # -- helpers --
    def _send(self, status=200, headers=None, body=b""):
        if isinstance(body, str):
            body = body.encode()
        self.send_response(status)
        headers = headers or {}
        if "Content-Length" not in headers:
            headers["Content-Length"] = str(len(body))
        # CORS: allow all
        headers.setdefault("Access-Control-Allow-Origin", "*")
        headers.setdefault("Access-Control-Allow-Methods", "*")
        headers.setdefault("Access-Control-Allow-Headers", "*")
        headers.setdefault("Access-Control-Expose-Headers", "*")
        for k, v in headers.items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _send_preflight(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "*")
        self.send_header("Access-Control-Allow-Headers", "*")
        self.send_header("Access-Control-Max-Age", "86400")
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _parse(self):
        u = urllib.parse.urlparse(self.path)
        return u.path, urllib.parse.parse_qs(u.query), u

    def _match_route(self, method, path):
        for r in self.routes:
            rm = r.get("method", "*").upper()
            if rm not in ("*", method):
                continue
            pat = r.get("path", "")
            # support exact + fnmatch wildcards
            if pat == path or fnmatch.fnmatch(path, pat):
                return r
        return None

    def _apply_wildcard(self, pattern, target, actual_path):
        # /blog/* -> /new-blog/*  preserves suffix
        if "*" not in pattern:
            return target
        # only support single trailing *
        if pattern.endswith("*"):
            prefix = pattern[:-1]
            suffix = actual_path[len(prefix):] if actual_path.startswith(prefix) else ""
            return target.replace("*", suffix)
        return target

    def _serve_route(self, route, path):
        delay = float(route.get("delay", 0))
        if delay:
            time.sleep(delay)

        status = int(route.get("status", 200))

        # meta-refresh simulation (HTML redirect, no 3xx)
        if "meta_refresh" in route:
            mr = route["meta_refresh"]
            to = mr.get("to", "/")
            secs = mr.get("seconds", 0)
            html = f'<!doctype html><meta http-equiv="refresh" content="{secs};url={to}"><p>Redirecting to <a href="{to}">{to}</a>...'
            return self._send(200, {"Content-Type": "text/html"}, html)

        loc = route.get("location")
        if loc and 300 <= status < 400:
            loc = self._apply_wildcard(route.get("path", ""), loc, path)
            body = route.get("body", f'Redirecting to <a href="{loc}">{loc}</a>')
            h = dict(route.get("headers", {}))
            h.setdefault("Location", loc)
            h.setdefault("Content-Type", "text/html")
            return self._send(status, h, body)

        # file body shortcut
        if "file" in route:
            fp = os.path.join(self.serve_dir, route["file"])
            if os.path.isfile(fp):
                with open(fp, "rb") as f:
                    data = f.read()
                h = dict(route.get("headers", {}))
                h.setdefault("Content-Type", mimetypes.guess_type(fp)[0] or "application/octet-stream")
                return self._send(status, h, data)

        body = route.get("body", "")
        h = dict(route.get("headers", {}))
        if isinstance(body, (dict, list)):
            body = json.dumps(body)
            h.setdefault("Content-Type", "application/json")
        return self._send(status, h, body)

    def _serve_static(self, path):
        # clean URLs: /v1.3/schema -> schema.html / schema.md / schema.json
        # versioned layout: /v1.3/... maps to ./v1.3/... on disk, no config needed
        # "/" renders harness index when no ./index.html exists
        rel = os.path.normpath(path.lstrip("/"))
        if rel == ".":
            rel = ""
        fp = os.path.join(self.serve_dir, rel)

        # "/" harness index fallback
        if rel == "" and os.path.isdir(fp):
            idx = os.path.join(fp, "index.html")
            if not os.path.isfile(idx):
                return self._serve_harness_index(fp)

        if os.path.isdir(fp):
            idx = os.path.join(fp, "index.html")
            if os.path.isfile(idx):
                fp = idx
            else:
                # extensionless dir index? try index.md
                idx_md = os.path.join(fp, "index.md")
                if os.path.isfile(idx_md):
                    with open(idx_md, "rb") as f:
                        return self._send(200, {"Content-Type": "text/markdown; charset=utf-8"}, f.read())
                try:
                    entries = os.listdir(fp)
                except FileNotFoundError:
                    return self._send(404, {"Content-Type": "text/plain"}, "Not Found")
                html = f"<h1>Index of /{rel}</h1><ul>"
                html += "".join(f'<li><a href="{e}">{e}</a></li>' for e in sorted(entries))
                return self._send(200, {"Content-Type": "text/html"}, html + "</ul>")
        if not os.path.isfile(fp):
            # extensionless clean-URL resolve
            for ext in (".html", ".md", ".json", ".txt"):
                if os.path.isfile(fp + ext):
                    fp = fp + ext
                    break
        # traversal guard: resolved path must stay under serve_dir
        if os.path.isfile(fp) and os.path.commonpath(
            [os.path.abspath(fp), self.serve_dir]
        ) == self.serve_dir:
            ctype = mimetypes.guess_type(fp)[0] or "application/octet-stream"
            if fp.endswith(".md"):
                ctype = "text/markdown; charset=utf-8"
            with open(fp, "rb") as f:
                return self._send(200, {"Content-Type": ctype}, f.read())
        return self._send(404, {"Content-Type": "text/plain"}, f"Not Found: {path}")

    def _serve_harness_index(self, root_fp):
        links = []
        for v in self.versions:
            vp = os.path.join(root_fp, v)
            mark = "" if os.path.isdir(vp) else " (missing dir)"
            links.append(f'<li><a href="/{v}/">/{v}/</a>{mark}</li>')
        # surface versioned routes too
        vroutes = "".join(
            f'<li><code>{r.get("method", "*")} {r.get("path")}</code> -&gt; {r.get("status", 200)} {r.get("location", "")}</li>'
            for r in self.routes[:50]
        )
        html = f"""<!doctype html><meta charset=utf-8><title>test harness</title>
<h1>test harness</h1>
<h2>versions</h2><ul>{"".join(links)}</ul>
<h2>sim endpoints (no prefix, CORS *)</h2>
<ul>
<li><code>/status/&lt;code&gt;</code></li>
<li><code>/redirect?to=/foo&amp;code=301</code></li>
<li><code>/delay/&lt;secs&gt;?then=/foo</code></li>
<li><code>/echo</code></li>
<li><code>/routes</code></li>
</ul>
<h2>routes ({len(self.routes)})</h2><ul>{vroutes or "<li>(none — add --config or --redirect)</li>"}</ul>
"""
        return self._send(200, {"Content-Type": "text/html; charset=utf-8"}, html)

    def _handle_builtins(self, path, qs):
        # accept both /status/.. and legacy /__status/.. (same for all builtins)
        # so everything is served from "/" root with no prefix required
        norm = path
        if norm.startswith("/__"):
            norm = "/" + norm[3:]
        # /status/404
        if norm.startswith("/status/"):
            try:
                code = int(norm.rsplit("/", 1)[-1])
            except ValueError:
                code = 200
            return self._send(code, {"Content-Type": "text/plain"}, f"Status {code}\n")

        # /redirect?to=/foo&code=301
        if norm == "/redirect":
            to = qs.get("to", ["/"])[0]
            code = int(qs.get("code", ["302"])[0])
            return self._send(code, {"Location": to, "Content-Type": "text/html"},
                              f'Redirecting to <a href="{to}">{to}</a>')

        # /delay/2?then=/foo
        if norm.startswith("/delay/"):
            try:
                secs = float(norm.split("/delay/")[1].split("/")[0])
            except ValueError:
                secs = 1
            time.sleep(secs)
            then = qs.get("then", [None])[0]
            if then:
                return self._send(302, {"Location": then}, f"Delayed redirect to {then}")
            return self._send(200, {"Content-Type": "text/plain"}, f"Delayed {secs}s\n")

        # /echo
        if norm == "/echo":
            length = int(self.headers.get("Content-Length", 0) or 0)
            raw = self.rfile.read(length) if length else b""
            dump = {
                "method": self.command,
                "path": self.path,
                "headers": dict(self.headers),
                "body": raw.decode(errors="replace"),
            }
            return self._send(200, {"Content-Type": "application/json"}, json.dumps(dump, indent=2))

        # /routes
        if norm == "/routes":
            return self._send(200, {"Content-Type": "application/json"},
                              json.dumps(self.routes, indent=2))
        return None

    def _handle(self):
        method = self.command
        path, qs, _ = self._parse()

        # CORS preflight — always allow
        if method == "OPTIONS":
            return self._send_preflight()

        # 1. builtins at "/" root (plus /__ aliases)
        handled = self._handle_builtins(path, qs)
        if handled is not None:
            return
        # unknown /__ path -> 404, but don't swallow real static files
        if path.startswith("/__"):
            norm = "/" + path[3:]
            if norm.startswith(("/status/", "/delay/")) or norm in ("/redirect", "/echo", "/routes"):
                self._send(404, {"Content-Type": "text/plain"}, "Unknown sim endpoint")
                return
            # else fall through to routes/static

        # 2. config routes
        route = self._match_route(method, path)
        if route:
            print(f"  -> ROUTE {route} ")
            self._serve_route(route, path)
            return

        # 3. static fallback
        self._serve_static(path)

    do_GET = _handle
    do_POST = _handle
    do_PUT = _handle
    do_DELETE = _handle
    do_PATCH = _handle
    do_HEAD = _handle
    do_OPTIONS = _handle


def load_routes(path, versions=None):
    if not path:
        return []
    with open(path) as f:
        data = json.load(f)
    routes = data if isinstance(data, list) else data.get("routes", [])
    # expand {"version": "v1.3"|["v1.3","v1.4"], "path": "/old", ...}
    # into one route per version: path+location prefixed with /<version>
    out = []
    for r in routes:
        v = r.get("version")
        if not v:
            out.append(r)
            continue
        vs = [v] if isinstance(v, str) else list(v)
        if "*" in vs and versions:
            vs = list(versions)
        for one in vs:
            if one == "*":
                out.append({k: x for k, x in r.items() if k != "version"})
                continue
            cp = dict(r)
            cp.pop("version", None)
            cp["path"] = f"/{one}{r.get('path', '')}"
            if "location" in r and isinstance(r["location"], str) and r["location"].startswith("/"):
                cp["location"] = f"/{one}{r['location']}"
            out.append(cp)
    return out


def main():
    ap = argparse.ArgumentParser(description="Static + redirect simulator (versioned test harness)")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--host", default="0.0.0.0")
    ap.add_argument("--dir", default=".", help="dir to serve statically (expects v1.3/ v1.4/ subdirs)")
    ap.add_argument("--versions", default="v1.3,v1.4", help="comma-separated version prefixes")
    ap.add_argument("--config", default=None, help="routes.json")
    ap.add_argument("--redirect", action="append", default=[],
                    help="shorthand OLD:NEW[:CODE], e.g. /old:/new:301 (repeatable)")
    args = ap.parse_args()

    versions = [v.strip().strip("/") for v in args.versions.split(",") if v.strip()]
    routes = load_routes(args.config, versions) if args.config else []

    for r in args.redirect:
        # /old:/new:301
        parts = r.split(":")
        old, new = parts[0], parts[1] if len(parts) > 1 else "/"
        code = int(parts[2]) if len(parts) > 2 else 301
        routes.append({"path": old, "status": code, "location": new})

    SimHandler.routes = routes
    SimHandler.serve_dir = os.path.abspath(args.dir)
    SimHandler.versions = versions

    srv = ThreadingHTTPServer((args.host, args.port), SimHandler)
    print(f"Serving {SimHandler.serve_dir} on http://{args.host}:{args.port} (versions: {', '.join(versions)})")
    print(f"{len(routes)} route(s) loaded.")
    print("Root: / (harness index)  Built-ins at root: /status/CODE  /redirect?to=&code=  /delay/N  /echo  /routes")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nbye.")


if __name__ == "__main__":
    main()
