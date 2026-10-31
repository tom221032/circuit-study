#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vf_ocr.py —— 对抽到的帧做本地 OCR（RapidOCR），生成候选卡片素材。

    python scripts/vf_ocr.py            # 处理 _frames/index.json 里所有帧
    python scripts/vf_ocr.py --limit 40 # 只跑前 40 张（调试）

产物：
    _frames/ocr/pNN.json   [{frame,t,chapter,lines:[{text,score,box}],text,score,hits}]
    _frames/ocr/summary.json  {frames,byChapter,kept}
hits 是粗筛关键词（结论/注意/小结/要点/公式/记住/易错…），供后续蒸馏挑重点。
"""
from __future__ import annotations

import json
import re
import sys
import time

import cv2
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FR = ROOT / "_frames"
OCR_DIR = FR / "ocr"

CUE = ("结论", "注意", "小结", "总结", "要点", "公式", "记住", "易错", "切记", "推论",
       "定理", "性质", "特点", "条件", "必须", "只能", "不能", "一定", "归纳", "口诀",
       "结论是", "所以", "因此", "可见", "说明", "提示")

_engine = None


def engine():
    global _engine
    if _engine is None:
        from rapidocr_onnxruntime import RapidOCR
        # 2 线程：批量任务按「多进程 × 2 线程」吃满 20 核，比单进程多吃线程更快
        _engine = RapidOCR(intra_op_num_threads=2)
    return _engine


OCR_W = 800  # 下采样宽度：1080p 帧缩到 1024 宽再识别，速度快一倍多、精度几乎不变


def ocr_image(path: Path) -> list[dict]:
    img = cv2.imread(str(path))
    if img is not None and img.shape[1] > OCR_W:
        h = int(round(img.shape[0] * OCR_W / img.shape[1]))
        img = cv2.resize(img, (OCR_W, h), interpolation=cv2.INTER_AREA)
    res, _ = engine()(img if img is not None else str(path))
    out = []
    for item in res or []:
        box, text, score = item[0], item[1], item[2]
        text = (text or "").strip()
        if not text:
            continue
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        out.append({"text": text, "score": round(float(score), 3),
                    "box": [round(min(xs)), round(min(ys)), round(max(xs)), round(max(ys))]})
    # 按行（y 中心）排序，行内按 x 排序，拼成阅读顺序
    out.sort(key=lambda r: ((r["box"][1] + r["box"][3]) / 2, r["box"][0]))
    return out


def page_text(lines: list[dict]) -> str:
    """把 OCR 行按 y 聚成视觉行，行内按 x 排序后用空格连接。"""
    if not lines:
        return ""
    buckets: list[list[dict]] = []
    for ln in lines:
        cy = (ln["box"][1] + ln["box"][3]) / 2
        if buckets and abs(cy - buckets[-1][0]["_cy"]) < 14:
            buckets[-1].append({**ln, "_cy": cy})
        else:
            buckets.append([{**ln, "_cy": cy}])
    rows = []
    for b in buckets:
        b.sort(key=lambda r: r["box"][0])
        rows.append(" ".join(r["text"] for r in b))
    return chr(10).join(rows)


def hits_of(text: str) -> list[str]:
    return [k for k in CUE if k in text]


def main(argv: list[str]) -> int:
    limit = None
    if "--limit" in argv:
        limit = int(argv[argv.index("--limit") + 1])
    pages = [int(a) for a in argv if a.isdigit()]
    rows = json.loads((FR / "index.json").read_text(encoding="utf-8"))
    if not rows:
        print("_frames/index.json 为空，先跑 vf_frames.py")
        return 1
    if pages:
        rows = [r for r in rows if int(r["page"]) in pages]
    if limit:
        rows = rows[:limit]
    OCR_DIR.mkdir(parents=True, exist_ok=True)

    pick = "--pick" in argv
    STRONG = ("结论", "注意", "小结", "总结", "要点", "记住", "易错", "切记", "推论", "归纳", "口诀")
    if pick:
        from collections import Counter
        cap = Counter()
        kept_rows = []
        for r in sorted(rows, key=lambda r: (int(r["page"]), r["t"])):
            ch = int(r["chapter"])
            if cap[ch] >= 32:
                continue
            if any(k in (r.get("text") or "") for k in STRONG):
                kept_rows.append(r)
                cap[ch] += 1
        rows = kept_rows
        print(f"精选用帧 {len(rows)} 张（窗文本含结论/注意类关键词，每章上限 32）", flush=True)

    by_page: dict[int, list[dict]] = defaultdict(list)
    for r in rows:
        by_page[int(r["page"])].append(r)

    t0 = time.time()
    done = 0
    for page, items in sorted(by_page.items()):
        dst = OCR_DIR / f"p{page:02d}.json"
        if dst.exists() and "--force" not in argv:
            continue
        out = []
        for r in items:
            img = FR / "frames" / r["frame"]
            if not img.exists():
                continue
            lines = ocr_image(img)
            text = page_text(lines)
            out.append({"page": int(r["page"]), "frame": r["frame"], "t": r["t"],
                        "chapter": r["chapter"],
                        "lines": lines, "text": text,
                        "score": round(sum(l["score"] for l in lines) / max(1, len(lines)), 3),
                        "hits": hits_of(text)})
            done += 1
        old = json.loads(dst.read_text(encoding="utf-8")) if dst.exists() else []
        dst.write_text(
            json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
        print(f"  P{page:02d} {len(out)} 帧 OCR 完成（累计 {done}/{len(rows)}，{time.time()-t0:.0f}s）",
              flush=True)

    allrows = []
    for f in sorted(OCR_DIR.glob("p*.json")):
        allrows.extend(json.loads(f.read_text(encoding="utf-8")))
    allrows.sort(key=lambda r: (r.get("chapter", 0), r.get("t", 0)))
    by_ch = Counter(r["chapter"] for r in allrows)
    kept = sum(1 for r in allrows if r["hits"])
    if not pages:
        (OCR_DIR / "summary.json").write_text(json.dumps(
            {"frames": len(allrows), "byChapter": dict(sorted(by_ch.items())), "withCue": kept},
            ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"完成 {len(allrows)} 帧；含结论/注意类关键词 {kept} 帧；按章 {dict(sorted(by_ch.items()))}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
