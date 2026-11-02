# -*- coding: utf-8 -*-
"""
对外接口（HTTP JSON API）
=========================
启动方式：主界面「开放本地接口」开关，或命令行 python -m app.main --api 8765
接口列表：
  GET  /api/status                 查看运行状态
  POST /api/desensitize            {"paths":[...], "options":{...}} 批量去敏
  GET  /api/default-options        返回默认参数
这样其他程序（脚本、内部系统）可以直接复用本工具的处理能力。
"""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import APP_NAME, APP_VERSION
from .engine import Options, process_batch, list_images, SUPPORTED_EXT

_lock = threading.Lock()
_server = None
_thread = None
_busy = False


def _expand_paths(paths):
    out = []
    for p in paths:
        if os_is_dir(p):
            out.extend(list_images(p))
        elif p.lower().endswith(SUPPORTED_EXT):
            out.append(p)
    return out


def os_is_dir(p):
    import os
    return os.path.isdir(p)


class _Handler(BaseHTTPRequestHandler):
    server_version = "PhotoPrivacy/%s" % APP_VERSION

    def log_message(self, fmt, *args):
        pass

    def _json(self, code, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.split("?")[0] == "/api/status":
            self._json(200, {
                "ok": True, "app": APP_NAME, "version": APP_VERSION,
                "busy": _busy, "supported": list(SUPPORTED_EXT),
            })
        elif self.path.split("?")[0] == "/api/default-options":
            self._json(200, {"ok": True, "options": Options().to_dict()})
        else:
            self._json(404, {"ok": False, "error": "未知接口，可用：/api/status、/api/desensitize、/api/default-options"})

    def do_POST(self):
        global _busy
        if self.path.split("?")[0] != "/api/desensitize":
            self._json(404, {"ok": False, "error": "未知接口"})
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            raw = self.rfile.read(length) if length else b"{}"
            data = json.loads(raw.decode("utf-8"))
        except Exception as e:
            self._json(400, {"ok": False, "error": "请求体不是合法 JSON：%s" % e})
            return
        paths = _expand_paths(data.get("paths") or [])
        if not paths:
            self._json(400, {"ok": False, "error": "paths 为空，或路径不存在"})
            return
        options = Options.from_dict(data.get("options") or {})
        if not options.output_dir:
            options.output_dir = data.get("output") or ""
        if not _lock.acquire(blocking=False):
            self._json(429, {"ok": False, "error": "正在处理另一个任务，请稍后再试"})
            return
        _busy = True
        try:
            summary = process_batch(paths, options)
            self._json(200, {"ok": True, "summary": summary})
        except Exception as e:
            self._json(500, {"ok": False, "error": "%s: %s" % (type(e).__name__, e)})
        finally:
            _busy = False
            _lock.release()


def start(port=8765):
    """启动本地接口服务（守护线程），返回实际端口或 None。"""
    global _server, _thread
    if _server is not None:
        return port
    try:
        _server = ThreadingHTTPServer(("127.0.0.1", int(port)), _Handler)
        _thread = threading.Thread(target=_server.serve_forever, daemon=True)
        _thread.start()
        return int(port)
    except Exception:
        _server = None
        return None


def stop():
    global _server, _thread
    if _server is not None:
        try:
            _server.shutdown()
            _server.server_close()
        except Exception:
            pass
    _server = None
    _thread = None


def running():
    return _server is not None
