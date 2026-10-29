# -*- coding: utf-8 -*-
"""抓取 B 站合集的 80 集元数据 -> data/videos.json"""
import json
import re
import sys
import urllib.request
from pathlib import Path

BVID = "BV1jHLGzDEJe"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/156.0.0.0 Safari/537.36 Edg/156.0.0.0")
ROOT = Path(__file__).resolve().parent.parent

CN = "零一二三四五六七八九十"


def cn2int(s: str) -> int:
    if not s:
        return 0
    if s == "十":
        return 10
    if s.startswith("十"):
        return 10 + CN.index(s[1])
    if "十" in s:
        a, b = s.split("十")
        return CN.index(a) * 10 + (CN.index(b) if b else 0)
    return CN.index(s[0])


def get(url: str):
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Referer": f"https://www.bilibili.com/video/{BVID}/",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.loads(r.read().decode("utf-8"))


def main():
    data = get(f"https://api.bilibili.com/x/web-interface/view?bvid={BVID}")
    if data.get("code") != 0:
        sys.exit(f"API error: {data}")
    d = data["data"]
    pages = d["pages"]
    videos = []
    for p in pages:
        part = p["part"]
        m = re.match(r"^(\d+)\s*第(.+?)章\s*(.*)$", part)
        ch_no, ch_name = 0, ""
        if m:
            ch_no, ch_name = cn2int(m.group(2)), m.group(3).strip()
        videos.append({
            "page": p["page"],
            "cid": p["cid"],
            "part": part,
            "chapter": ch_no,
            "chapterName": ch_name,
            "duration": p["duration"],
            "title": re.sub(r"^\d+\s*", "", part) or part,
            "url": f"https://www.bilibili.com/video/{BVID}/?p={p['page']}",
            "embed": (f"https://player.bilibili.com/player.html?bvid={BVID}"
                      f"&cid={p['cid']}&p={p['page']}&autoplay=0&high_quality=1"),
        })
    out = {
        "bvid": BVID,
        "aid": d["aid"],
        "title": "电路 · 石群主讲 邱关源《电路》第六版（80 集 4K 修复）",
        "titleRaw": d["title"],
        "desc": d.get("desc", ""),
        "owner": d["owner"]["name"],
        "cover": d["pic"],
        "total": len(videos),
        "totalDuration": sum(v["duration"] for v in videos),
        "videos": videos,
    }
    (ROOT / "data").mkdir(exist_ok=True)
    (ROOT / "data" / "videos.json").write_text(
        json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"OK {len(videos)} videos, total {out['totalDuration']/3600:.1f} h")
    missing = [v["page"] for v in videos if not v["chapter"]]
    print("without chapter:", missing)


if __name__ == "__main__":
    main()
