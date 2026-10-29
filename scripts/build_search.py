# -*- coding: utf-8 -*-
"""生成全站搜索索引 data/search.json（笔记 / 习题 / 真题 / 字幕）"""
import html
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
BLOCK_RE = re.compile(r'<(p|h2|h3|figure|figcaption)[^>]*?(?: id="([^"]*)")?[^>]*>(.*?)</\1>', re.S)
KP_RE = re.compile(
    r'<aside class="kp k-[a-z]+" id="([^"]+)">\s*<div class="kp-h">\s*'
    r'<span class="kp-kind">([^<]+)</span>\s*<span class="kp-title">([^<]*)</span></div>\s*'
    r'<div class="kp-t">([^<]*)</div>\s*'
    r'<a class="kp-src" href="#/video/(\d+)\?t=(\d+)"', re.S)
DETAILS_RE = re.compile(r"<details[\s\S]*?</details>", re.S)


def text_of(fragment: str) -> str:
    t = re.sub(r"<img[^>]*>", "", fragment)
    t = re.sub(r"<[^>]+>", "", t)
    return html.unescape(re.sub(r"\s+", " ", t)).strip()


def blocks_of(html_str: str):
    for m in BLOCK_RE.finditer(html_str):
        text = text_of(m.group(3))
        if len(text) >= 12:
            yield m.group(2) or "", text


def main():
    items = []
    video_by_chapter = {}

    for path in sorted((DATA / "content").glob("ch*.json")):
        ch = json.loads(path.read_text(encoding="utf-8"))
        n, ctitle = ch["n"], ch["title"]
        for sec in ch["sections"]:
            kind = sec["kind"]
            if kind == "notes":
                html_str = sec.get("html", "")
                for anchor, text in blocks_of(html_str):
                    for i in range(0, len(text), 160):
                        chunk = text[i:i + 200]
                        items.append({"k": "note", "c": n, "ct": ctitle, "p": sec["page"],
                                      "a": anchor, "x": chunk})
                for m in KP_RE.finditer(html_str):
                    anchor, kd, title, body, vpage, vt = m.groups()
                    items.append({"k": "kp", "c": n, "ct": ctitle, "p": sec["page"],
                                  "a": anchor, "kd": kd, "v": int(vpage), "t": int(vt),
                                  "x": (title + " " + body)[:300]})
            elif kind == "ex":
                for it in sec.get("items", []):
                    q = text_of(it["q"])
                    if len(q) >= 8:
                        items.append({"k": "ex", "c": n, "ct": ctitle, "p": sec["page"],
                                      "no": it["no"], "x": q[:400]})
            else:
                for g in sec.get("groups", []):
                    # 折叠起来的答案与解析不进索引，避免搜索时直接泄题
                    for anchor, text in blocks_of(DETAILS_RE.sub("", g.get("html", ""))):
                        items.append({"k": "exam", "c": n, "ct": ctitle, "p": sec["page"], "x": text[:300]})

    subs_dir = DATA / "subs"
    sub_index, n_subs, n_lines = {}, 0, 0
    if subs_dir.exists():
        for path in sorted(subs_dir.glob("*.json")):
            if path.name in ("status.json", "index.json"):
                continue
            d = json.loads(path.read_text(encoding="utf-8"))
            page, lines = d["page"], d["lines"]
            sub_index[page] = {"cid": d["cid"], "n": len(lines)}
            n_subs += 1
            n_lines += len(lines)
            step = 4
            for i in range(0, len(lines), step):
                grp = lines[i:i + 6]
                txt = " ".join(g["text"] for g in grp)
                if len(txt) < 10:
                    continue
                items.append({"k": "sub", "v": page, "t": grp[0]["t"], "x": txt[:300]})
        (subs_dir / "index.json").write_text(json.dumps(sub_index, ensure_ascii=False), encoding="utf-8")

    (DATA / "search.json").write_text(json.dumps({"items": items}, ensure_ascii=False), encoding="utf-8")
    size = (DATA / "search.json").stat().st_size / 1e6
    print(f"search items: {len(items)}  ({size:.1f} MB)  字幕 {n_subs} 集 / {n_lines} 句")


if __name__ == "__main__":
    main()
