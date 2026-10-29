"""抓取 B 站 AI 字幕（需要已登录的 DSH 浏览器窗口）

原理：连上 DSH（wincu）保留登录态的 Edge 的 CDP 端口，在 bilibili.com 源下
用页面内 fetch 依次请求 player/wbi/v2 拿到字幕地址，再取字幕 JSON。
这样不需要任何 cookie 传参，也不会把登录信息写进聊天里。

用法：
  python scripts/fetch_subs.py            # 补齐缺少的字幕
  python scripts/fetch_subs.py --force    # 全部重抓
  python scripts/fetch_subs.py --page 1   # 只抓第 1 集（自检用）
"""
import argparse
import json
import os
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
SUBS = DATA / "subs"
BVID = "BV1jHLGzDEJe"
PROFILE = Path(os.path.expanduser(
    r"~\AppData\Local\dsh-computer-use-win\agent-browser\kept-edge"))

JS = """
(async () => {
  const cid = __CID__;
  const url = `https://api.bilibili.com/x/player/wbi/v2?bvid=%s&cid=${cid}`;
  const r = await fetch(url, {credentials: 'include'}).then(x => x.json());
  const out = {code: r.code, needLogin: r.data && r.data.need_login_subtitle};
  const list = (r.data && r.data.subtitle && r.data.subtitle.subtitles) || [];
  out.count = list.length;
  out.lan = list.map(s => s.lan + '/' + s.lan_doc);
  if (list.length) {
    const s = list.find(x => x.lan.startsWith('ai')) || list[0];
    out.url = s.subtitle_url;
    try {
      const j = await fetch(out.url).then(x => x.json());
      out.body = j.body || [];
    } catch (e) { out.err = String(e); }
  }
  return JSON.stringify(out);
})()
""" % BVID


def cdp_port():
    f = PROFILE / "DevToolsActivePort"
    if not f.exists():
        sys.exit("找不到 DevToolsActivePort：" + str(f) + "\n（先在 DSH 浏览器窗口里打开一次 B 站）")
    return int(f.read_text(encoding="utf-8").splitlines()[0].strip())


def http_json(port, path):
    with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=10) as r:
        return json.loads(r.read().decode("utf-8"))


def page_ws(port):
    """找一个 bilibili 页面的 CDP WebSocket；没有就在后台标签打开一个"""
    targets = http_json(port, "/json/list")
    cand = [t for t in targets if t.get("type") == "page" and "bilibili.com" in t.get("url", "")]
    if not cand:
        req = urllib.request.Request(
            f"http://127.0.0.1:{port}/json/new?https://www.bilibili.com/video/{BVID}/",
            method="PUT")
        with urllib.request.urlopen(req, timeout=15) as r:
            cand = [json.loads(r.read().decode("utf-8"))]
        time.sleep(2.5)
    return cand[0]["webSocketDebuggerUrl"]


class CDP:
    def __init__(self, ws_url):
        from websockets.sync.client import connect
        self.ws = connect(ws_url, max_size=64 * 1024 * 1024, open_timeout=15)
        self.id = 0

    def call(self, method, params=None, timeout=60):
        self.id += 1
        mid = self.id
        self.ws.send(json.dumps({"id": mid, "method": method, "params": params or {}}))
        while True:
            msg = json.loads(self.ws.recv(timeout=timeout))
            if msg.get("id") == mid:
                if "error" in msg:
                    raise RuntimeError(msg["error"])
                return msg.get("result", {})

    def eval(self, expr, timeout=90):
        r = self.call("Runtime.evaluate",
                      {"expression": expr, "awaitPromise": True, "returnByValue": True},
                      timeout=timeout)
        res = r.get("result", {})
        if "exceptionDetails" in r:
            raise RuntimeError(json.dumps(r["exceptionDetails"], ensure_ascii=False)[:400])
        return res.get("value")

    def close(self):
        try:
            self.ws.close()
        except Exception:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--force", action="store_true")
    ap.add_argument("--page", type=int, default=0)
    args = ap.parse_args()

    videos = json.loads((DATA / "videos.json").read_text(encoding="utf-8"))
    if isinstance(videos, dict):
        videos = videos.get("videos") or videos.get("items")
    SUBS.mkdir(parents=True, exist_ok=True)

    todo = [v for v in videos if args.page in (0, v["page"])]
    if not args.force:
        todo = [v for v in todo if not (SUBS / f"{v['cid']}.json").exists()]
    if not todo:
        print("全部字幕都已存在，无需抓取")
        return

    port = cdp_port()
    cdp = CDP(page_ws(port))
    print(f"已连接 CDP :{port}，待抓 {len(todo)} 集")
    ok = fail = 0
    try:
        for i, v in enumerate(todo, 1):
            expr = JS.replace("__CID__", str(v["cid"]), 1)
            try:
                raw = cdp.eval(expr)
                d = json.loads(raw)
            except Exception as e:
                print(f"  [{i}/{len(todo)}] P{v['page']:02d} 异常 {e}")
                fail += 1
                time.sleep(1.2)
                continue
            body = d.get("body") or []
            tag = f"  [{i}/{len(todo)}] P{v['page']:02d} {v['part'][:26]}"
            if body:
                lan = (d.get("lan") or ["ai-zh"])[0]
                rec = {"page": v["page"], "cid": v["cid"], "part": v["part"],
                       "chapter": v["chapter"], "chapterName": v["chapterName"],
                       "lan": lan, "count": len(body),
                       "lines": [{"t": round(s["from"], 2), "text": s["content"].strip()}
                                 for s in body if s.get("content", "").strip()]}
                (SUBS / f"{v['cid']}.json").write_text(
                    json.dumps(rec, ensure_ascii=False), encoding="utf-8")
                ok += 1
                print(f"{tag} ✓ {len(body)} 句")
            else:
                fail += 1
                hint = "需登录" if d.get("needLogin") else ("无字幕" if d.get("code") == 0 else f"code={d.get('code')}")
                print(f"{tag} ✗ {hint}")
            time.sleep(1.0)
    finally:
        cdp.close()
    print(f"完成：成功 {ok}，失败 {fail}")
    if fail and ok == 0:
        sys.exit(2)


if __name__ == "__main__":
    main()
