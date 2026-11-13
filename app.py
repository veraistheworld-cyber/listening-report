#!/usr/bin/env python3
"""Minimal playlist-report entry MVP. Stdlib only."""

from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from analyze import analyze_playlist
from fetchers import FetchError, fetch_playlist

ROOT = Path(__file__).resolve().parent
STATIC = ROOT / "static"
MAX_BODY = 4 * 1024 * 1024
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765


def _json_bytes(payload: dict, status: int = 200) -> tuple[int, bytes]:
    return status, json.dumps(payload, ensure_ascii=False).encode("utf-8")


class Handler(BaseHTTPRequestHandler):
    server_version = "PlaylistReportMVP/0.1"

    def log_message(self, fmt: str, *args) -> None:
        sys.stderr.write("%s - %s\n" % (self.log_date_time_string(), fmt % args))

    def _cors(self) -> None:
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")

    def do_OPTIONS(self) -> None:  # noqa: N802
        self.send_response(204)
        self._cors()
        self.end_headers()

    def do_GET(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path in ("/", "/index.html"):
            return self._send_file(STATIC / "index.html", "text/html; charset=utf-8")
        if path == "/api/health":
            return self._send_json({"ok": True, "service": "playlist-report"}, 200)
        # Static files only from ./static
        if path.startswith("/static/"):
            rel = path[len("/static/") :]
        else:
            rel = path.lstrip("/")
        target = (STATIC / rel).resolve()
        try:
            target.relative_to(STATIC.resolve())
        except ValueError:
            return self._send_json({"error": "未找到该页面。"}, 404)
        if target.is_file():
            ctype, _ = mimetypes.guess_type(str(target))
            if target.suffix == ".css":
                ctype = "text/css; charset=utf-8"
            elif target.suffix == ".js":
                ctype = "application/javascript; charset=utf-8"
            elif target.suffix == ".html":
                ctype = "text/html; charset=utf-8"
            return self._send_file(target, ctype or "application/octet-stream")
        self._send_json({"error": "未找到该页面。"}, 404)

    def do_POST(self) -> None:  # noqa: N802
        path = urlparse(self.path).path
        if path == "/api/fetch-playlist":
            return self._fetch()
        if path == "/api/analyze":
            return self._analyze()
        self._send_json({"error": "未知接口。"}, 404)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            raise FetchError("请求内容过大。")
        raw = self.rfile.read(length) if length else b"{}"
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise FetchError("请求需要是 JSON，例如 {\"url\": \"...\"} 或 {\"text\": \"...\"}。") from exc
        if not isinstance(data, dict):
            raise FetchError("请求 JSON 必须是对象。")
        return data

    def _fetch(self) -> None:
        try:
            body = self._read_json()
            result = fetch_playlist(url=body.get("url") or "", text=body.get("text") or "")
            self._send_json(result, 200)
        except FetchError as exc:
            self._send_json({"error": exc.message}, exc.http_status)
        except Exception:
            sys.stderr.write("internal error in /api/fetch-playlist\n")
            import traceback

            traceback.print_exc()
            self._send_json({"error": "服务暂时出错，请稍后重试。"}, 500)

    def _analyze(self) -> None:
        try:
            body = self._read_json()
            result = analyze_playlist(body)
            self._send_json(result, 200)
        except FetchError as exc:
            self._send_json({"error": exc.message}, exc.http_status)
        except Exception:
            sys.stderr.write("internal error in /api/analyze\n")
            import traceback

            traceback.print_exc()
            self._send_json({"error": "服务暂时出错，请稍后重试。"}, 500)

    def _send_json(self, payload: dict, status: int) -> None:
        status, blob = _json_bytes(payload, status)
        self.send_response(status)
        self._cors()
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(blob)))
        self.end_headers()
        self.wfile.write(blob)

    def _send_file(self, path: Path, content_type: str) -> None:
        if not path.is_file():
            return self._send_json({"error": "未找到该页面。"}, 404)
        data = path.read_bytes()
        self.send_response(200)
        self._cors()
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def _env_port() -> int | None:
    raw = os.environ.get("PORT")
    if raw in (None, ""):
        return None
    return int(raw)


def main() -> None:
    env_port = _env_port()
    parser = argparse.ArgumentParser(description="听歌报告入口 MVP")
    parser.add_argument(
        "--host",
        default="0.0.0.0" if env_port is not None else DEFAULT_HOST,
        help=f"监听地址，默认 {DEFAULT_HOST}；设置 PORT 时为 0.0.0.0",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=env_port if env_port is not None else DEFAULT_PORT,
        help=f"端口，默认 {DEFAULT_PORT}，或环境变量 PORT",
    )
    args = parser.parse_args()
    httpd = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"听歌报告入口已启动：http://{args.host}:{args.port}/", flush=True)
    print("按 Ctrl+C 结束。", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\n已停止。", flush=True)
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
