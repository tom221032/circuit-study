# -*- coding: utf-8 -*-
"""本地静态服务：python scripts/serve.py [端口]，自动用 Edge 打开"""
import http.server
import os
import socketserver
import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8123


class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(ROOT), **kw)

    def log_message(self, fmt, *args):
        if args and str(args[0]).startswith(("4", "5")):
            print("  " + fmt % args)

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()


def open_browser(url):
    candidates = [
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if os.path.exists(c):
            subprocess.Popen([c, url])
            return True
    return webbrowser.open(url)


def already_running(url):
    import urllib.request
    try:
        with urllib.request.urlopen(url, timeout=0.8) as r:
            return r.status == 200
    except Exception:
        return False


def main():
    url = f"http://127.0.0.1:{PORT}/"
    if already_running(url):
        print(f"{url} 已经在运行了（可能是之前开的窗口还没关）。")
        print("这次只把浏览器打开，不再启动第二个服务。")
        if os.environ.get("CIRCUIT_NO_OPEN") != "1":
            open_browser(url)
        return
    socketserver.ThreadingTCPServer.allow_reuse_address = True
    try:
        server = socketserver.ThreadingTCPServer(("127.0.0.1", PORT), Handler)
    except OSError as e:
        print(f"端口 {PORT} 绑定失败：{e}")
        print(f"换一个端口试试：python scripts\\serve.py 8124")
        return
    with server as httpd:
        print(f"电路学习站已启动：{url}")
        print("（关闭这个窗口即停止服务）")
        if os.environ.get("CIRCUIT_NO_OPEN") != "1":
            threading.Timer(0.6, lambda: open_browser(url)).start()
        try:
            httpd.serve_forever()
        except KeyboardInterrupt:
            print("\n已停止")


if __name__ == "__main__":
    main()
