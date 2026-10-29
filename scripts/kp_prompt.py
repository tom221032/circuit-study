# -*- coding: utf-8 -*-
"""把候选窗口 + 本章笔记小标题组装成「归纳提示词」文件。

用法:
    python scripts/kp_prompt.py          # 全部有候选的章
    python scripts/kp_prompt.py 1 8

产物:
    data/kp/prompt/chNN.md   交给模型归纳的完整输入（读它 -> 写 raw/chNN.json）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
CAND = DATA / "kp" / "candidates"
PROMPT = DATA / "kp" / "prompt"

TEMPLATE = """# 任务
你是《电路》（邱关源第六版）课程的助教。下面是石群老师第 {n} 章「{title}」视频的 AI 字幕候选片段（自动提取，含口语碎片和错字）。
请提炼视频里讲的**知识点、结论、推论、易错提醒、常用公式、解题技巧**，输出严格 JSON。

## 本章笔记已有的小标题（heading 必须原样取自这一列表，或用 null）
{headings}

## 硬性要求
1. 只写字幕里确实讲到的内容，不得补充你自带的知识；每条都要能在候选片段里找到依据。
2. 每条 `text` ≤ 80 个汉字，写成可直接复习的完整陈述句（有条件就写条件，有结论就写结论）；不要「老师讲到……」「这里……」这类转述。
3. `kind` 只能是：结论 / 推论 / 注意 / 公式 / 技巧。
4. `heading` 从上面列表里挑最贴切的一条（连同「一、」「1 」这类前缀原样复制）；实在无法归类填 null。
5. `src` 给出依据片段的时间：`{{"page": 集号, "t": 秒}}`，`t` 取片段标注 `[pX@mm:ss]` 换算后的秒数。
6. 去重：同一结论跨集重复只留最早一条；课本标题本身就已经说清的白话常识不要写。
7. 条数 10–20 条（本章素材少时至少 5 条），按重要性从高到低排；口水话直接丢弃。
8. 顺手纠正 ASR 错字：电流、电压、相量（不是向量）、戴维南、基尔霍夫、阻抗、感抗、容抗、谐振、幅值、相位、叠加。
9. 输出里不要出现 Markdown、不要解释、不要代码块围栏，只输出 JSON。

## 输出格式（严格 JSON）
{{"n": {n}, "points": [
  {{"kind": "结论", "title": "不超过 16 字的标题", "text": "完整陈述句。", "heading": "一、电压和电流的参考方向", "src": {{"page": 1, "t": 312}}}}
]}}

## 候选片段（每行 `[p集号@时:分:秒]` + 原话）
{body}
"""


def toc_headings(n):
    path = DATA / "content" / f"ch{n:02d}.json"
    if not path.exists():
        return []
    ch = json.loads(path.read_text(encoding="utf-8"))
    for sec in ch["sections"]:
        if sec["kind"] == "notes":
            return [t["text"].strip() for t in sec.get("toc", [])]
    return []


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    chapters = sorted(int(a) for a in args) if args else list(range(1, 19))
    PROMPT.mkdir(parents=True, exist_ok=True)

    for n in chapters:
        cand_path = CAND / f"ch{n:02d}.json"
        if not cand_path.exists():
            print(f"ch{n:02d} 跳过（无候选）")
            continue
        cand = json.loads(cand_path.read_text(encoding="utf-8"))
        heads = toc_headings(n)
        head_txt = "\n".join(f"- {h}" for h in heads) or "- （本章无小标题，heading 一律填 null）"

        body = []
        for blk in cand["windows"]:
            for w in blk["wins"]:
                body.append(f"[p{blk['page']}@{hms(w['t0'])}] {w['text']}")
        text = TEMPLATE.format(n=n, title=cand["title"], headings=head_txt,
                               body="\n".join(body))
        (PROMPT / f"ch{n:02d}.md").write_text(text, encoding="utf-8")
        print(f"ch{n:02d}  prompt {len(text)} 字 / 小标题 {len(heads)} / 片段 {len(body)}")


def hms(t):
    t = int(t)
    h, m, s = t // 3600, (t % 3600) // 60, t % 60
    return f"{h}:{m:02d}:{s:02d}"


if __name__ == "__main__":
    main()
