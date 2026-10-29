# -*- coding: utf-8 -*-
"""从 AI 字幕里挖「知识点候选」：cue 句 + 上下文，带时间戳标记。

用法:
    python scripts/kp_mine.py              # 全部 18 章
    python scripts/kp_mine.py 1 8          # 只做第 1、8 章
    python scripts/kp_mine.py --stats       # 只打印统计

产物:
    data/kp/candidates/chNN.json   结构化候选窗口
    data/kp/candidates/chNN.md     人工可读版（带 [p1@00:12] 标记）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
KDIR = DATA / "kp" / "candidates"

# 强信号：老师在下结论 / 提醒 / 给口诀
STRONG = re.compile(
    r"结论|推论|总结|小结|记住|务必|注意|易错|容易错|口诀|技巧|原则|本质上|关键在于|关键是|"
    r"推广|由此可见|归纳|规律|值得|特别提醒|不要|不能|必须|一定要|统一|都适用|不失一般性")
# 中信号：推导收尾 / 公式 / 定义
MEDIUM = re.compile(
    r"所以|因此|于是|定理|定律|公式|等于|代入|解得|条件|前提|区别|关系|等价|对应|"
    r"定义|概念|性质|表示|写成|表达式")
# 考试相关
EXAM = re.compile(r"考试|考点|真题|考研|大题|小题|题型|易考")
FILLER = re.compile(r"^(对|啊|嗯|哦|那个|这个|是吧|对吧|好|行|那么|来)[，,。 ]*$")
GLOSSARY_PATH = ROOT / "scripts" / "kp_glossary.json"

CAP_CHARS = 16000          # 每章候选字数上限（控制归纳调用体量）
MAX_WIN_PER_EP = 26        # 每集候选窗口上限


def load_glossary():
    if not GLOSSARY_PATH.exists():
        return []
    data = json.loads(GLOSSARY_PATH.read_text(encoding="utf-8"))
    return [(d["wrong"], d["right"]) for d in data.get("terms", [])]


def fix_terms(text, glossary):
    for wrong, right in glossary:
        if wrong in text:
            text = text.replace(wrong, right)
    return text


def score_of(text):
    s = 0
    if STRONG.search(text):
        s += 3
    if MEDIUM.search(text):
        s += 1
    if EXAM.search(text):
        s += 2
    if len(text) >= 14:
        s += 1
    return s


def episodes_of(videos, chapter):
    return [v for v in videos if v["chapter"] == chapter]


def load_subs(video):
    """字幕按 cid 命名（data/subs/<cid>.json）。"""
    path = DATA / "subs" / f"{video['cid']}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def mmss(t):
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def mine_episode(video, subs, glossary):
    lines = []
    for ln in subs["lines"]:
        text = fix_terms(ln["text"].strip(), glossary)
        if not text or FILLER.match(text):
            continue
        lines.append({"t": int(ln["t"]), "text": text})
    if not lines:
        return []

    scored = []
    for i, ln in enumerate(lines):
        sc = score_of(ln["text"])
        if sc >= 3:
            scored.append((sc, i))
    scored.sort(key=lambda x: (-x[0], x[1]))
    picked = sorted(i for _, i in scored[:MAX_WIN_PER_EP])

    spans = []
    for i in picked:
        a, b = max(0, i - 3), min(len(lines) - 1, i + 5)
        if spans and a <= spans[-1][1] + 1:
            spans[-1] = (spans[-1][0], max(spans[-1][1], b))
        else:
            spans.append((a, b))

    out = []
    for a, b in spans:
        chunk = lines[a:b + 1]
        text = " ".join(x["text"] for x in chunk)
        if len(text) < 30:
            continue
        out.append({
            "t0": chunk[0]["t"],
            "t1": chunk[-1]["t"],
            "text": text,
            "score": max(score_of(x["text"]) for x in chunk),
        })
    return out


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    stats_only = "--stats" in sys.argv
    videos = json.loads((DATA / "videos.json").read_text(encoding="utf-8"))["videos"]
    glossary = load_glossary()
    chapters = sorted(int(a) for a in args) if args else list(range(1, 19))
    KDIR.mkdir(parents=True, exist_ok=True)

    total_win = total_chars = 0
    for ch in chapters:
        eps = episodes_of(videos, ch)
        title = eps[0]["chapterName"] if eps else f"第{ch}章"
        rec = {"n": ch, "title": title, "episodes": [], "missing": [], "truncated": False}
        flat = []
        for v in eps:
            subs = load_subs(v)
            if not subs:
                rec["missing"].append(v["page"])
                continue
            wins = mine_episode(v, subs, glossary)
            for w in wins:
                w["page"] = v["page"]
                flat.append(w)
            rec["episodes"].append({
                "page": v["page"],
                "part": v["part"],
                "duration": v["duration"],
                "windows": len(wins),
            })

        flat.sort(key=lambda w: (w["page"], w["t0"]))
        chars = sum(len(w["text"]) for w in flat)
        if chars > CAP_CHARS:
            rec["truncated"] = True
            ranked = sorted(flat, key=lambda w: (-w["score"], w["page"], w["t0"]))
            keep = []
            used = 0
            for w in ranked:
                if used + len(w["text"]) > CAP_CHARS:
                    continue
                keep.append(w)
                used += len(w["text"])
            flat = sorted(keep, key=lambda w: (w["page"], w["t0"]))

        by_page = {}
        for w in flat:
            by_page.setdefault(w["page"], []).append(w)
        rec["windows"] = [{"page": p, "wins": ws} for p, ws in sorted(by_page.items())]
        rec["count"] = len(flat)
        rec["chars"] = sum(len(w["text"]) for w in flat)

        total_win += rec["count"]
        total_chars += rec["chars"]
        print(f"ch{ch:02d}  {rec['count']:4d} 窗 / {rec['chars']:6d} 字"
              f"  缺字幕 {rec['missing'] or '-'}{'  (截断)' if rec['truncated'] else ''}")

        if stats_only:
            continue

        (KDIR / f"ch{ch:02d}.json").write_text(
            json.dumps(rec, ensure_ascii=False, indent=1), encoding="utf-8")

        md = [f"# 第 {ch} 章 {title} — 视频知识点候选", ""]
        for blk in rec["windows"]:
            md.append(f"## 第 {blk['page']} 集")
            for w in blk["wins"]:
                md.append(f"- [p{blk['page']}@{mmss(w['t0'])}] {w['text']}")
            md.append("")
        (KDIR / f"ch{ch:02d}.md").write_text("\n".join(md), encoding="utf-8")

    print(f"\n合计 {total_win} 窗 / {total_chars} 字" +
          ("" if stats_only else f"  ->  {KDIR}"))


if __name__ == "__main__":
    main()
