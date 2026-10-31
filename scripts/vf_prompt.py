#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vf_prompt.py —— 把「帧 OCR + 字幕窗口文本」整理成每章一份的蒸馏提示词。

    python scripts/vf_prompt.py            # 全 18 章
    python scripts/vf_prompt.py 1 12       # 只这几章

输入：
    _frames/ocr/pNN.json          帧 OCR（含 text / hits / t / chapter）
    _frames/index.json            帧清单（frame / t / win / text）
    data/content/chNN.json        notes 的 toc（小标题清单）
    data/kp/chNN.json             已有文本知识点（避免重复）
输出：
    data/kp/prompt_visual/chNN.md
"""
from __future__ import annotations

import json
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
FR = ROOT / "_frames"
OUT = DATA / "kp" / "prompt_visual"
MAX_FRAMES = 70          # 每章最多给模型看的帧（按 hits 优先 + 时间均匀）
MAX_TEXT = 520           # 单帧 OCR 文本上限
MAX_SUB = 160            # 单帧附带的字幕窗口文本上限


def load(p: Path, default=None):
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else default


def mmss(t: float) -> str:
    t = int(t)
    return f"{t // 60}:{t % 60:02d}"


def clean(s: str) -> str:
    s = re.sub(r"[ \t]+", " ", s or "")
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def main(argv: list[str]) -> int:
    wanted = [int(a) for a in argv if a.isdigit()]
    ocr = defaultdict(list)
    for f in sorted((FR / "ocr").glob("p*.json")):
        page = int(f.stem[1:])
        for row in json.loads(f.read_text(encoding="utf-8")):
            row["page"] = int(row.get("page") or page)
            ocr[int(row["chapter"])].append(row)

    OUT.mkdir(parents=True, exist_ok=True)
    for ch_path in sorted((DATA / "content").glob("ch*.json")):
        ch = json.loads(ch_path.read_text(encoding="utf-8"))
        n = int(ch["n"])
        if wanted and n not in wanted:
            continue
        notes = next(s for s in ch["sections"] if s["kind"] == "notes")
        toc = [t["text"] for t in (notes.get("toc") or []) if t.get("level", 2) <= 2]
        old = load(DATA / "kp" / f"ch{n:02d}.json", {"points": []})["points"]
        rows = ocr.get(n, [])
        if not rows:
            print(f"ch{n:02d} 没有 OCR 帧，跳过")
            continue

        # 选帧：先按 hits 命中数降序，再按时间均匀补齐
        rows.sort(key=lambda r: (-len(r["hits"]), r["t"]))
        top = [r for r in rows if r["hits"]][: MAX_FRAMES // 2]
        rest = [r for r in rows if not r["hits"]]
        step = max(1, len(rest) // max(1, MAX_FRAMES - len(top)))
        picked = top + rest[::step]
        picked = sorted({r["frame"]: r for r in picked}.values(), key=lambda r: r["t"])[:MAX_FRAMES]

        lines = [f"# 第 {n} 讲 · {ch['title']} —— 板书帧蒸馏任务", "",
                 "## 这一章的笔记小标题（heading 必须原样取自这里，或填 null）"]
        for t in toc:
            lines.append(f"- {t}")
        lines += ["", "## 这一章已有的知识点标题（不要重复，可细化但不要同义改写）"]
        for p in old:
            lines.append(f"- [{p['kind']}] {p['title']}")
        lines += ["", f"## 板书帧（共 {len(picked)} 张，来自视频截图 OCR；`[pNN@mm:ss ↓命中词]`）"]
        for r in picked:
            hits = "".join(r["hits"][:4])
            sub = clean(r.get("text", ""))[:MAX_SUB]
            body = clean(r.get("text", ""))
            # 帧自身的 OCR 文本在 ocr 里；字幕窗口文本在 index 里
            lines.append(f"### {r['frame']} [p{int(r['page']):02d}@{mmss(r['t'])}"
                          + (f" ↓{hits}" if hits else "") + "]")
            lines.append("```")
            lines.append(body[:MAX_TEXT])
            lines.append("```")

        lines += ["", "## 输出要求（务必严格遵守）", """
1. 只输出一个 JSON 对象，不要 markdown 代码围栏，不要解释：
   {"n": <章号>, "title": "<章名>", "points": [ ... ]}
2. points 每项字段：`kind`（只能是 结论/推论/注意/公式/技巧）、`title`（≤18 汉字）、
   `text`（≤90 汉字，必须来自帧上的板书或字幕原话，可压缩但不要改意思）、
   `heading`（原样取自上面的小标题清单，找不到就 null）、`frame`（对应帧文件名，如 `p01_000205.jpg`）、
   `src`（{"page": 集号, "t": 秒}，秒数由该帧的 `@mm:ss` 换算）。
3. 条数 8–20，**优先「结论」「注意」**：板书上带「结论/注意/小结/要点/公式」字样的、被框起来的、
   带红笔标注的，优先收。不要收纯定义、纯例题过程。
4. `text` 要写成能独立读懂的一句话，别抄成碎片；公式用普通文本（如 `uC=US(1-e^(-t/τ))`）。
5. 同一张帧最多出 2 条；`frame` 必须是上面真实出现过的文件名。
6. 与「已有知识点标题」重复的不要写；但可以用截图给出更具体、更可操作的表述。
7. 不做跨章串味：只用本章帧。
"""]
        (OUT / f"ch{n:02d}.md").write_text("\n".join(lines), encoding="utf-8")
        print(f"ch{n:02d} 帧 {len(rows)} 张 → 选 {len(picked)} 张，提示词 "
              f"{(OUT / f'ch{n:02d}.md').stat().st_size} 字节")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
