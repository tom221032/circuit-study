"""vf_cards.py —— 把板书帧 OCR 结果直接整理成「视频截图卡片」候选。

与 vf_prompt.py + 子代理蒸馏的路线并行：本脚本不调用任何模型，
只用规则挑帧、清洗 OCR 文本、按关键词分类，输出 data/kp/raw_visual/chNN.json，
再交给 kp_merge_visual.py 注入到章节笔记里。

用法：
    python scripts/vf_cards.py            # 全部章节
    python scripts/vf_cards.py 1 12       # 只处理指定章
    python scripts/vf_cards.py --cap 10   # 每章最多几张卡（默认 12）
"""
from __future__ import annotations

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

try:
    import cv2

    HAVE_CV2 = True
except Exception:  # 没有 opencv 时退化为只按标题去重
    HAVE_CV2 = False

ROOT = Path(__file__).resolve().parent.parent
FR = ROOT / "_frames"
DATA = ROOT / "data"
KP = DATA / "kp"

WATERMARK = re.compile(
    r"(bilibili|B站|大学电气课堂|电路\s*石群|石群|电气课堂|优优|关注|订阅|"
    r"^\d{1,2}:\d{2}(:\d{2})?$|^第\s*\d+\s*集|^P?\d{1,2}\s*$)", re.I)
CJK = re.compile(r"[\u4e00-\u9fff]")

CUES_CONCL = ("结论", "小结", "总结", "归纳", "要点")
CUES_WARN = ("注意", "易错", "切记", "必须", "不能")
CUES_FORM = ("公式", "定理", "定律", "方程", "表达式")
CUES_INFER = ("推论", "推广", "可推出", "由此", "可见")


def cjk_len(s: str) -> int:
    return len(CJK.findall(s))


def drop_set() -> dict[int, set]:
    """人工删除清单 data/kp/visual_drop.json → {章号: {帧文件名}}"""
    path = KP / "visual_drop.json"
    out: dict[int, set] = {}
    if not path.exists():
        return out
    try:
        items = json.loads(path.read_text(encoding="utf-8")).get("items") or []
    except Exception:
        return out
    for it in items:
        if it.get("ch") and it.get("frame"):
            out.setdefault(int(it["ch"]), set()).add(it["frame"])
    return out


def clean_lines(text: str) -> list[str]:
    out = []
    for line in (text or "").splitlines():
        t = line.strip()
        if not t:
            continue
        if WATERMARK.search(t):
            continue
        if cjk_len(t) < 2 and not re.search(r"[A-Za-z]{2,}", t):
            continue
        # 去掉 OCR 常见的碎片符号
        t = re.sub(r"^[\(\[（【]?\s*[+\-—·•]?\s*", "", t)
        t = re.sub(r"\s{2,}", " ", t).strip()
        if t:
            out.append(t)
    return out


def join_text(lines: list[str]) -> str:
    """把 OCR 行拼成通顺的一段：中文之间不加空格，英文/数字之间保留空格。"""
    buf = ""
    for t in lines:
        if not buf:
            buf = t
            continue
        if re.search(r"[A-Za-z0-9]$", buf) and re.match(r"[A-Za-z0-9]", t):
            buf += " " + t
        else:
            buf += t
    return re.sub(r"\s{2,}", " ", buf).strip()


def trunk_at_cue(body: str) -> str:
    """板书帧常带上一张幻灯片的残影：在提示词附近截断，从「注意/结论…」开始。"""
    head = body[:60]
    idx = -1
    for cue in CUES_CONCL + CUES_WARN:
        j = head.find(cue)
        if j >= 0 and (idx < 0 or j < idx):
            idx = j
    if idx > 0:
        return body[idx:]
    return body


def classify(text: str) -> str:
    if any(k in text for k in CUES_CONCL):
        return "结论"
    if any(k in text for k in CUES_WARN):
        return "注意"
    if any(k in text for k in CUES_INFER):
        return "推论"
    if any(k in text for k in CUES_FORM) or "=" in text or "＝" in text:
        return "公式"
    return "技巧"


def bigrams(s: str) -> set[str]:
    s = re.sub(r"[\s\W_]+", "", s or "")
    return {s[i:i + 2] for i in range(len(s) - 1)}


def match_heading(text: str, toc: list[str]) -> str | None:
    tb = bigrams(text)
    best, score = None, 0
    for h in toc:
        hb = bigrams(h)
        if not hb:
            continue
        n = len(hb & tb)
        if n > score:
            best, score = h, n
    return best if score >= 2 else None


def short_heading(h: str) -> str:
    return re.sub(r"^\s*(\d+(?:\.\d+)+|[一二三四五六七八九十]+、)\s*", "", h or "").strip()


def dhash(path: Path, size: int = 8) -> int | None:
    """8x8 差分哈希，用来判断两张板书截图是不是同一张板。"""
    if not HAVE_CV2:
        return None
    img = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
    if img is None:
        return None
    img = cv2.resize(img, (size + 1, size), interpolation=cv2.INTER_AREA)
    bits = 0
    diff = img[:, 1:] > img[:, :-1]
    for b in diff.flatten():
        bits = (bits << 1) | int(b)
    return bits


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def norm_title(t: str) -> str:
    return re.sub(r"[\s\W_]+", "", t or "")


def pick_title(body: str, lines: list[str], kind: str, heading: str | None, cap: int = 18) -> str:
    """标题优先取板书上「提示词 + 紧跟的短句」，比小节名更贴近这张板。"""
    cues = CUES_CONCL + CUES_WARN
    for c in cues:
        j = body.find(c)
        if j < 0:
            continue
        tail = re.sub(r"^[\s①②③④⑤⑥⑦⑧⑨⑩、,，.。:：]+", "", body[j + len(c):])
        segs = [x for x in re.split(r"[；;，,。！!？?]", tail) if cjk_len(x) >= 3]
        if segs:
            head = max(segs[:3], key=cjk_len)
            return f"{c}：{head}"[:cap]
        return c
    if heading:
        return f"{kind}：{short_heading(heading)}"[:cap]
    cand = [t for t in lines if cjk_len(t) >= 4]
    return (max(cand, key=cjk_len) if cand else "板书要点")[:cap]


def pick_text(body: str, cap: int = 100) -> str:
    """正文取提示词起的 1–3 句，避免截断在半句中间。"""
    parts = re.split(r"(?<=[；;。！!？?])", body)
    out = ""
    for part in parts:
        if cjk_len(out) >= 24 and cjk_len(out + part) > cap:
            break
        out += part
        if cjk_len(out) >= cap:
            break
    return (out or body)[:cap]


REPORT: dict = {"dropped": [], "byChapter": {}}


def main(argv: list[str]) -> int:
    check_only = "--check" in argv
    argv = [a for a in argv if a != "--check"]
    cap = 12
    if "--cap" in argv:
        i = argv.index("--cap")
        cap = int(argv[i + 1])
        argv = argv[:i] + argv[i + 2:]
    wanted = [int(a) for a in argv if a.isdigit()]

    rows_by_ch: dict[int, list[dict]] = defaultdict(list)
    for f in sorted((FR / "ocr").glob("p*.json")):
        page = int(f.stem[1:])
        for row in json.loads(f.read_text(encoding="utf-8")):
            row["page"] = int(row.get("page") or page)
            rows_by_ch[int(row["chapter"])].append(row)

    out_dir = KP / "raw_visual"
    out_dir.mkdir(parents=True, exist_ok=True)
    total = 0
    for cpath in sorted((DATA / "content").glob("ch*.json")):
        ch = json.loads(cpath.read_text(encoding="utf-8"))
        n = int(ch["n"])
        if wanted and n not in wanted:
            continue
        notes = next(s for s in ch["sections"] if s["kind"] == "notes")
        toc = [t["text"] for t in (notes.get("toc") or []) if t.get("level", 2) <= 2]
        old = json.loads((KP / f"ch{n:02d}.json").read_text(encoding="utf-8"))["points"] \
            if (KP / f"ch{n:02d}.json").exists() else []
        old_titles = [p.get("title", "") for p in old]
        dropped_frames = drop_set().get(n, set())

        scored = []
        for r in rows_by_ch.get(n, []):
            frame = r["frame"]
            if frame in dropped_frames:
                continue
            if not (FR / "frames" / frame).exists():
                continue
            lines = clean_lines(r.get("text", ""))
            body = trunk_at_cue(join_text(lines))
            if cjk_len(body) < 24:
                continue
            hits = [h for h in (r.get("hits") or []) if h]
            strong = [k for k in CUES_CONCL + CUES_WARN if k in body]
            if not hits and not strong:
                continue
            score = 3 * len(hits) + 2 * len(strong) + min(cjk_len(body), 120) / 30
            scored.append({"row": r, "lines": lines, "body": body, "score": score})

        scored.sort(key=lambda x: (-x["score"], x["row"]["t"]))
        # 先把所有候选项都算成 point（不做上限裁剪），再去重
        cand = []
        for it in scored:
            r, body = it["row"], it["body"]
            frame = r["frame"]
            kind = classify(body)
            heading = match_heading(body + " " + " ".join(it["lines"]), toc)
            title = pick_title(body, it["lines"], kind, heading)
            if title in old_titles:
                continue
            cand.append({
                "kind": kind,
                "title": title,
                "text": pick_text(body),
                "heading": heading,
                "frame": frame,
                "src": {"page": r["page"], "t": int(r["t"])},
                "_score": it["score"],
            })

        points, seen_hash, seen_title, seen_text = [], [], {}, []
        dropped = []
        for pt in cand:
            if len(points) >= cap:
                break
            nt = norm_title(pt["title"])
            if nt in seen_title:
                dropped.append({"title": pt["title"], "frame": pt["frame"], "by": "title",
                                "kept": seen_title[nt]})
                continue
            if any(t[:16] == pt["text"][:16] for t in seen_text):
                dropped.append({"title": pt["title"], "frame": pt["frame"], "by": "text"})
                continue
            if any(f == pt["frame"] for (_h, f) in seen_hash):
                dropped.append({"title": pt["title"], "frame": pt["frame"],
                                "by": "frame", "kept": pt["frame"]})
                continue
            h = dhash(FR / "frames" / pt["frame"])
            dup = next((f for (hf, f) in seen_hash if h is not None and hf is not None
                        and hamming(hf, h) <= 6), None)
            if dup:
                dropped.append({"title": pt["title"], "frame": pt["frame"],
                                "by": "frame", "kept": dup})
                continue
            seen_hash.append((h, pt["frame"]))
            seen_title[nt] = pt["frame"]
            seen_text.append(pt["text"])
            points.append({k: v for k, v in pt.items() if k != "_score"})

        for d in dropped:
            d["ch"] = n
        if dropped:
            REPORT.setdefault("dropped", []).extend(dropped)

        if not points:
            print(f"ch{n:02d} 没有可用板书帧")
            continue
        doc = {"n": n, "title": ch["title"], "points": points}
        (out_dir / f"ch{n:02d}.json").write_text(
            json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
        total += len(points)
        REPORT["byChapter"][str(n)] = {"kept": len(points), "dropped": len(dropped)}
        print(f"ch{n:02d} {len(points)} 张卡片 {dict(Counter(p['kind'] for p in points))}"
              + (f"  去重丢 {len(dropped)}" if dropped else ""))
    print(f"合计 {total} 张，去重丢弃 {len(REPORT['dropped'])} 张")
    if check_only:
        for d in [x for x in REPORT["dropped"] if x["by"] != "cap"]:
            print(f"  丢弃 ch{d['ch']:02d} {d['by']:>5} {d['frame']} {d['title'][:24]}")
        return 0
    (KP / "dedupe_report.json").write_text(
        json.dumps(REPORT, ensure_ascii=False, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
