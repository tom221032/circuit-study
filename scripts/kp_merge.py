# -*- coding: utf-8 -*-
"""把模型归纳出的知识点校验、去重、锚定，注入章节笔记 HTML。

用法:
    python scripts/kp_merge.py            # 全部已归纳的章
    python scripts/kp_merge.py 1 8
    python scripts/kp_merge.py --check     # 只报告，不写文件

输入: data/kp/raw/chNN.json      （模型输出）
产物: data/kp/chNN.json          （最终卡片数据，随站点发布）
      data/kp/by-video.json      （集号 -> 知识点，供视频页交叉链接）
      data/kp/report.json        （校验统计）
      改写 data/content/chNN.json（注入 <aside class="kp">）
      改写 data/course.json      （chapters[].kp / kpTotal）
"""
import html
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KP = DATA / "kp"
RAW = KP / "raw"

KINDS = ["结论", "推论", "注意", "公式", "技巧"]
KIND_CLS = {"结论": "concl", "推论": "infer", "注意": "warn", "公式": "formula", "技巧": "tip"}
HEAD_RE = re.compile(r'<h([23]) class="blk-h[12]" id="([^"]+)">(.*?)</h\1>', re.S)
TAG_RE = re.compile(r"<[^>]+>")
OLD_KP_RE = re.compile(r'<aside class="kp[^"]*"[\s\S]*?</aside>', re.S)
HEAD_KP_RE = re.compile(r'<h3 class="blk-h2" id="c\d+kpend">[\s\S]*?</h3>', re.S)


def norm_text(s):
    s = html.unescape(s or "")
    s = re.sub(r"<[^>]+>", "", s)
    s = re.sub(r"[\s，。、；：！？,.;:!?“”\"'（）()【】\[\]—…·]+", "", s)
    return s


def norm_heading(s):
    """比对小标题：去掉「一、」「1 」「（1）」等前缀与空白。"""
    s = re.sub(r"\s+", "", s or "")
    s = re.sub(r"^[一二三四五六七八九十]+、", "", s)
    s = re.sub(r"^[（(]?[0-9０-９]+[）)、.．]", "", s)
    return s


def bigrams(s):
    return {s[i:i + 2] for i in range(len(s) - 1)} or {s}


def similar(a, b):
    if not a or not b:
        return 0.0
    A, B = bigrams(a), bigrams(b)
    return len(A & B) / len(A | B)


def mmss(t):
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def load_videos():
    vs = json.loads((DATA / "videos.json").read_text(encoding="utf-8"))["videos"]
    out = {}
    for v in vs:
        out.setdefault(v["chapter"], []).append(v)
    return out


def heading_index(sec_html):
    """锚点 -> (标题文本, 标题结束位置)"""
    idx = {}
    for m in HEAD_RE.finditer(sec_html):
        text = html.unescape(TAG_RE.sub("", m.group(3))).strip()
        idx[m.group(2)] = (text, m.end())
    return idx


def resolve_anchor(heading, toc):
    """把模型给的 heading 文本映射到笔记锚点。"""
    if not heading:
        return None, False
    h = norm_heading(heading)
    for item in toc:
        if item["text"].strip() == heading.strip():
            return item["anchor"], True
    for item in toc:
        if norm_heading(item["text"]) == h:
            return item["anchor"], True
    for item in toc:
        if h and (h in norm_heading(item["text"]) or norm_heading(item["text"]) in h):
            return item["anchor"], True
    best, score = None, 0.55
    for item in toc:
        s = similar(h, norm_heading(item["text"]))
        if s > score:
            best, score = item["anchor"], s
    return best, bool(best)


def card_html(pt):
    kind = pt["kind"]
    cls = KIND_CLS.get(kind, "tip")
    return (f'<aside class="kp k-{cls}" id="{pt["id"]}">'
            f'<div class="kp-h"><span class="kp-kind">{kind}</span>'
            f'<span class="kp-title">{html.escape(pt["title"])}</span></div>'
            f'<div class="kp-t">{html.escape(pt["text"])}</div>'
            f'<a class="kp-src" href="#/video/{pt["src"]["page"]}?t={int(pt["src"]["t"])}">'
            f'第 {pt["src"]["page"]} 集 · {mmss(pt["src"]["t"])} ↗</a></aside>')


def process_chapter(n, chapter, videos, check_only):
    raw_path = RAW / f"ch{n:02d}.json"
    if not raw_path.exists():
        return None
    raw = json.loads(raw_path.read_text(encoding="utf-8"))
    points = raw.get("points") or []

    cpath = DATA / "content" / f"ch{n:02d}.json"
    ch = json.loads(cpath.read_text(encoding="utf-8"))
    notes = next(s for s in ch["sections"] if s["kind"] == "notes")
    toc = notes.get("toc") or []
    eps = {v["page"]: v for v in videos.get(n, [])}
    hidx = heading_index(notes["html"])

    issues = {"invalid": 0, "dupe": 0, "unmatched": 0, "dupe_detail": []}
    kept = []
    for p in points:
        kind = p.get("kind", "")
        title = (p.get("title") or "").strip()
        text = (p.get("text") or "").strip()
        src = p.get("src") or {}
        page, t = int(src.get("page") or 0), int(src.get("t") or 0)
        if kind not in KINDS or not title or len(text) < 8 or len(text) > 130:
            issues["invalid"] += 1
            continue
        if page not in eps or not (0 < t <= eps[page]["duration"]):
            issues["invalid"] += 1
            continue
        nt = norm_text(text)
        if any(similar(nt, norm_text(k["text"])) > 0.72 for k in kept):
            issues["dupe"] += 1
            continue
        anchor, matched = resolve_anchor(p.get("heading"), toc)
        if anchor and anchor not in hidx:
            matched, anchor = False, None
        if not matched:
            issues["unmatched"] += 1
        kept.append({"kind": kind, "title": title, "text": text,
                     "anchor": anchor, "src": {"page": page, "t": t}})

    kept.sort(key=lambda p: (p["src"]["page"], p["src"]["t"]))
    for i, p in enumerate(kept, 1):
        p["id"] = f"kp-{n}-{i}"

    stats = {"n": n, "count": len(kept), "byKind": {},
             "missing_episodes": sorted(set(v["page"] for v in videos.get(n, [])) -
                                        {p["src"]["page"] for p in kept}),
             **issues}
    for p in kept:
        stats["byKind"][p["kind"]] = stats["byKind"].get(p["kind"], 0) + 1

    if not check_only:
        body = OLD_KP_RE.sub("", notes["html"])
        body = HEAD_KP_RE.sub("", body)
        hidx = heading_index(body)

        buckets, tail = {}, []
        for p in kept:
            if p["anchor"] and p["anchor"] in hidx:
                buckets.setdefault(p["anchor"], []).append(p)
            else:
                tail.append(p)

        out, last = [], 0
        for anchor, (_text, end) in sorted(hidx.items(), key=lambda kv: kv[1][1]):
            if anchor not in buckets:
                continue
            cards = "".join(card_html(p) for p in buckets[anchor])
            out.append(body[last:end])
            out.append(cards)
            last = end
        out.append(body[last:])
        body = "".join(out)

        if tail:
            body += (f'<h3 class="blk-h2" id="c{n}kpend">视频补充结论（来自石群讲稿）</h3>'
                     + "".join(card_html(p) for p in tail))

        notes["html"] = body
        cpath.write_text(json.dumps(ch, ensure_ascii=False, indent=1), encoding="utf-8")
        (KP / f"ch{n:02d}.json").write_text(json.dumps(
            {"n": n, "title": ch["title"], "points": kept},
            ensure_ascii=False, indent=1), encoding="utf-8")

    return stats, kept, ch["title"]


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    check_only = "--check" in sys.argv
    chapters = sorted(int(a) for a in args) if args else list(range(1, 19))
    videos = load_videos()

    course_path = DATA / "course.json"
    course = json.loads(course_path.read_text(encoding="utf-8"))
    by_video = {}
    reports = []
    total = 0
    for n in chapters:
        got = process_chapter(n, course["chapters"][n - 1], videos, check_only)
        if not got:
            print(f"ch{n:02d} 跳过（无 raw 归纳结果）")
            continue
        stats, kept, title = got
        total += stats["count"]
        reports.append(stats)
        print(f"ch{n:02d} {title[:12]:<12} {stats['count']:3d} 条"
              f"  未锚定 {stats['unmatched']} / 非法 {stats['invalid']}"
              f" / 重复 {stats['dupe']}   {stats['byKind']}")
        if not check_only:
            course["chapters"][n - 1]["kp"] = {"count": stats["count"],
                                               "byKind": stats["byKind"]}
            for p in kept:
                by_video.setdefault(str(p["src"]["page"]), []).append(
                    {"c": n, "anchor": p["id"], "title": p["title"],
                     "t": p["src"]["t"], "kind": p["kind"]})

    print(f"\n合计 {total} 条" + ("（check 模式，未写文件）" if check_only else ""))
    if check_only:
        return

    KP.mkdir(parents=True, exist_ok=True)
    course["kpTotal"] = sum(c.get("kp", {}).get("count", 0) for c in course["chapters"])
    course_path.write_text(json.dumps(course, ensure_ascii=False, indent=1), encoding="utf-8")
    (KP / "by-video.json").write_text(json.dumps(by_video, ensure_ascii=False, indent=1),
                                      encoding="utf-8")
    (KP / "report.json").write_text(json.dumps(
        {"total": total, "chapters": reports}, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"course.kpTotal = {course['kpTotal']}，by-video {len(by_video)} 集")


if __name__ == "__main__":
    main()
