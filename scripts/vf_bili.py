#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vf_bili.py —— B 站取流的公共库（wbi 签名 + 浏览器 cookie + playurl）。

cookie 来源：DSH agent 浏览器（kept-edge）内存，通过 CDP `Network.getAllCookies` 读取，
不读磁盘 cookie 库、不解密。用完后只保存在进程内存里。

直接用命令行也能看一集的可用清晰度：
    python scripts/vf_bili.py 1 2 3
"""
from __future__ import annotations

import hashlib
import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

from websockets.sync.client import connect

ROOT = Path(__file__).resolve().parent.parent
BVID = "BV1jHLGzDEJe"
PORT_FILE = Path.home() / "AppData/Local/dsh-computer-use-win/agent-browser/kept-edge/DevToolsActivePort"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/141.0 Safari/537.36")

# wbi 混排表（B 站前端固定常量）
MIXIN_TAB = [46, 47, 18, 2, 53, 8, 23, 32, 15, 50, 10, 31, 58, 3, 45, 35, 27, 43, 5, 49, 33, 9,
             42, 19, 29, 28, 14, 39, 12, 38, 41, 13, 37, 48, 7, 16, 24, 55, 40, 61, 26, 17, 0, 1,
             60, 51, 30, 4, 22, 25, 54, 21, 56, 59, 6, 63, 57, 62, 11, 36, 20, 34, 44, 52]


# ---------------------------------------------------------------- CDP / cookies
def cdp_port() -> int:
    if not PORT_FILE.exists():
        raise RuntimeError(
            f"找不到 {PORT_FILE}。请先在 DSH 浏览器里打开一次 "
            f"https://www.bilibili.com/video/{BVID}/ 并确认已登录。")
    return int(PORT_FILE.read_text(encoding="utf-8").splitlines()[0].strip())


class _CDP:
    def __init__(self, port: int):
        lst = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/json/list", timeout=6)
                         .read().decode("utf-8"))
        page = next((t for t in lst if t.get("type") == "page" and "bilibili.com" in (t.get("url") or "")), None)
        if page is None:
            raise RuntimeError("DSH 浏览器里没有打开着的 bilibili 页面，请先打开视频页。")
        self.ws = connect(page["webSocketDebuggerUrl"], max_size=16 * 1024 * 1024)
        self.mid = 0

    def cmd(self, method: str, params=None):
        self.mid += 1
        self.ws.send(json.dumps({"id": self.mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv())
            if msg.get("id") == self.mid:
                return msg

    def close(self):
        try:
            self.ws.close()
        except Exception:  # noqa: BLE001
            pass


def bili_cookies() -> dict[str, str]:
    """从 DSH 浏览器内存里取 bilibili 域下的 cookie。

    """
    port = cdp_port()
    c = _CDP(port)
    try:
        c.cmd("Network.enable")
        res = c.cmd("Network.getAllCookies").get("result", {}).get("cookies", [])
    finally:
        c.close()
    out: dict[str, str] = {}
    for ck in res:
        if "bilibili" in (ck.get("domain") or ""):
            out[ck["name"]] = ck["value"]
    if "SESSDATA" not in out:
        raise RuntimeError("浏览器 cookie 里没有 SESSDATA（可能未登录 B 站）。")
    return out


def cookie_header(ck: dict[str, str]) -> str:
    return "; ".join(f"{k}={v}" for k, v in ck.items())


# ---------------------------------------------------------------- HTTP / wbi
def _headers(ck: dict[str, str] | None) -> dict[str, str]:
    h = {"User-Agent": UA, "Referer": f"https://www.bilibili.com/video/{BVID}/",
         "Origin": "https://www.bilibili.com"}
    if ck:
        h["Cookie"] = cookie_header(ck)
    return h


def get_text(url: str, ck: dict[str, str] | None = None, timeout: int = 20) -> str:
    req = urllib.request.Request(url, headers=_headers(ck))
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8", "replace")


def get_json(url: str, ck: dict[str, str] | None = None) -> dict:
    return json.loads(get_text(url, ck))


def wbi_keys(ck: dict[str, str]) -> str:
    nav = get_json("https://api.bilibili.com/x/web-interface/nav", ck)
    if nav.get("code") != 0 or not nav.get("data", {}).get("isLogin"):
        raise RuntimeError(f"nav 未登录：code={nav.get('code')} msg={nav.get('message')}")
    w = nav["data"]["wbi_img"]
    img = w["img_url"].rsplit("/", 1)[-1].split(".")[0]
    sub = w["sub_url"].rsplit("/", 1)[-1].split(".")[0]
    raw = img + sub
    return "".join(raw[i] for i in MIXIN_TAB if i < len(raw))[:32]


def sign_query(params: dict, mixin_key: str) -> str:
    p = {k: str(v) for k, v in params.items()}
    p["wts"] = str(int(time.time()))
    q = urllib.parse.urlencode(sorted(p.items()))
    q = "".join(ch for ch in q if ch not in "!'()*")
    rid = hashlib.md5((q + mixin_key).encode()).hexdigest()
    return f"{q}&w_rid={rid}"


def playurl(cid: int, qn: int, ck: dict[str, str], mixin_key: str) -> dict:
    qs = sign_query({"bvid": BVID, "cid": cid, "fnval": 16, "fourk": 1, "qn": qn}, mixin_key)
    return get_json("https://api.bilibili.com/x/player/wbi/playurl?" + qs, ck)


def best_video(data: dict, want_qn: int) -> dict:
    """挑 avc(codecid=7)、清晰度最接近 want_qn 的一条。"""
    vids = (data.get("dash") or {}).get("video") or []
    if not vids:
        raise RuntimeError("playurl 没有返回 dash.video（可能被风控或未登录）")
    avc = [v for v in vids if int(v.get("codecid", 0)) == 7] or vids
    return min(avc, key=lambda v: (abs(int(v.get("id", 0)) - want_qn), -int(v.get("bandwidth") or 0)))


def video_url(info: dict) -> str:
    return info.get("baseUrl") or info.get("base_url") or ""


def audio_url(data: dict) -> str | None:
    auds = (data.get("dash") or {}).get("audio") or []
    if not auds:
        return None
    auds.sort(key=lambda a: -int(a.get("bandwidth") or 0))
    return auds[0].get("baseUrl") or auds[0].get("base_url")


def probe(pages: list[int]) -> int:
    videos = json.loads((ROOT / "data" / "videos.json").read_text(encoding="utf-8"))["videos"]
    by_page = {int(v["page"]): v for v in videos}
    ck = bili_cookies()
    mixin = wbi_keys(ck)
    print(f"cookie {len(ck)} 个，wbi key 就绪；目标 {len(pages) or len(by_page)} 集\n")
    for page in (pages or sorted(by_page)[:3]):
        v = by_page.get(page)
        if not v:
            print(f"[skip] P{page} 不在 videos.json")
            continue
        for qn in (80, 64, 32):
            try:
                j = playurl(int(v["cid"]), qn, ck, mixin)
                if j.get("code") != 0:
                    raise RuntimeError(f"code={j.get('code')} {j.get('message')}")
                best = best_video(j["data"], qn)
                print(f"P{page:02d} 请求 {qn} → 得到 {best['id']} "
                      f"{best.get('width')}x{best.get('height')} codecid={best.get('codecid')} "
                      f"{(int(best.get('bandwidth') or 0))//1000} kbps")
                break
            except Exception as e:  # noqa: BLE001
                print(f"P{page:02d} 请求 {qn} 失败：{type(e).__name__}: {e}")
        time.sleep(0.8)
    return 0


if __name__ == "__main__":
    import os
    import sys

    raise SystemExit(probe([int(a) for a in sys.argv[1:]]))
