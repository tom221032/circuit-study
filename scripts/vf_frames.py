#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vf_frames.py —— 只在字幕 cue 窗口内抽帧（HTTP Range 按窗口取流），dHash 去重后落盘。

相比「整集下载再切」的做法，这里对每个窗口直接
    ffmpeg -ss <t0> -t <t1-t0> -i <480p/720p m4s URL> -vf select...
只取那一段字节，一座 80 集的带宽从 ~8.8GB 降到 ~0.5GB。

用法：
    python scripts/vf_frames.py            # 全部集
    python scripts/vf_frames.py 1 2 3      # 指定集
    python scripts/vf_frames.py --qn 32    # 用 480p（更快，字大也能 OCR）

产物：
    _frames/frames/pNN_HHMMSS.jpg
    _frames/idx/pNN.json       每集抽帧清单
    _frames/done/pNN           完成标记
    _frames/index.json         汇总（merge_index）
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import cv2
import imageio_ffmpeg
import numpy as np

import vf_bili as B

ROOT = Path(__file__).resolve().parent.parent
FR = ROOT / "_frames"
FRAMES = FR / "frames"
IDX_DIR = FR / "idx"
DONE_DIR = FR / "done"
MANIFEST = FR / "index.json"

STEP = 20
MAX_W = 1280
DHASH_T = 5
CHAPTER_CAP = 900
FFMPEG = imageio_ffmpeg.get_ffmpeg_exe()
QN = 64  # 720p：讲稿板书够清楚，单集码率 ~95kbps


def load_json(p: Path, default):
    if p.exists():
        return json.loads(p.read_text(encoding="utf-8"))
    return default


def save_json(p: Path, obj):
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def candidate_windows() -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    for f in sorted((ROOT / "data" / "kp" / "candidates").glob("ch*.json")):
        d = json.loads(f.read_text(encoding="utf-8"))
        ch = int(d["n"])
        for pb in d.get("windows", []):
            page = int(pb["page"])
            for w in pb.get("wins", []):
                out.setdefault(page, []).append(
                    {"t0": float(w["t0"]), "t1": float(w["t1"]),
                     "text": w.get("text", ""), "chapter": ch})
    for v in out.values():
        v.sort(key=lambda w: w["t0"])
    return out


def ff_headers(ck: dict[str, str]) -> list[str]:
    cookie = "; ".join(f"{k}={v}" for k, v in ck.items())
    return ["-user_agent", B.UA,
            "-referer", f"https://www.bilibili.com/video/{B.BVID}/",
            "-headers", f"Cookie: {cookie}\r\n"]


def probe_fps(url: str, ck: dict[str, str]) -> float:
    cmd = [FFMPEG, "-hide_banner", *ff_headers(ck), "-i", url, "-t", "0", "-f", "null", "-"]
    r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    for line in (r.stderr or "").splitlines():
        if " fps" in line:
            try:
                return float(line.split(" fps")[0].split(",")[-1].strip())
            except Exception:  # noqa: BLE001
                pass
    return 12.0


def grab_window(url: str, ck: dict[str, str], t0: float, dur: float, outdir: Path) -> int:
    vf = f"select='not(mod(n\\,{STEP}))',scale='min({MAX_W}\\,iw)':-2"
    cmd = [FFMPEG, "-hide_banner", "-loglevel", "error", "-y",
           *ff_headers(ck), "-ss", f"{t0:.2f}", "-t", f"{max(dur,1.0):.2f}", "-i", url,
           "-vf", vf, "-vsync", "0", "-q:v", "3", str(outdir / "f_%04d.jpg")]
    subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace")
    return len(list(outdir.glob("f_*.jpg")))


def dhash(img: np.ndarray) -> int:
    g = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    small = cv2.resize(g, (9, 8), interpolation=cv2.INTER_AREA)
    bits = (small[:, 1:] > small[:, :-1]).flatten()
    v = 0
    for b in bits:
        v = (v << 1) | int(b)
    return v


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def process_page(page: int, video: dict, windows: list[dict], ck, mixin, qn=QN) -> int:
    chapter = windows[0]["chapter"] if windows else 0
    j = B.playurl(int(video["cid"]), qn, ck, mixin)
    if j.get("code") != 0:
        print(f"  [{page:02d}] playurl 失败 code={j.get('code')} {j.get('message')}")
        return 0
    best = B.best_video(j["data"], qn)
    url = B.video_url(best)
    fps = probe_fps(url, ck)
    t_start = time.time()

    kept: list[dict] = []
    seen: list[int] = []
    for win in windows:
        dur = max(1.0, win["t1"] - win["t0"])
        with tempfile.TemporaryDirectory(dir=str(FR)) as td:
            n = grab_window(url, ck, win["t0"], dur, Path(td))
            files = sorted(Path(td).glob("f_*.jpg"))
            for k, fp in enumerate(files):
                img = cv2.imread(str(fp))
                if img is None:
                    continue
                dh = dhash(img)
                if any(hamming(dh, s) <= DHASH_T for s in seen[-12:]):
                    continue
                seen.append(dh)
                t = win["t0"] + k * STEP / fps
                name = f"p{page:02d}_{int(t)//3600:02d}{int(t)//60%60:02d}{int(t)%60:02d}.jpg"
                shutil.move(str(fp), str(FRAMES / name))
                kept.append({"page": page, "t": round(t, 2), "chapter": chapter, "frame": name,
                             "win": [round(win["t0"], 1), round(win["t1"], 1)], "text": win["text"]})
                if len(kept) >= CHAPTER_CAP:
                    break
        if len(kept) >= CHAPTER_CAP:
            print(f"  [{page:02d}] 达到每章 {CHAPTER_CAP} 帧上限，停止本集")
            break

    save_json(IDX_DIR / f"p{page:02d}.json", kept)
    (DONE_DIR / f"p{page:02d}").write_text(time.strftime("%Y-%m-%d %H:%M:%S"), encoding="utf-8")
    print(f"  [{page:02d}] {best.get('width')}x{best.get('height')} fps={fps:.2f} "
          f"窗口 {len(windows)} → 保留 {len(kept)} 帧（{time.time()-t_start:.0f}s）", flush=True)
    return len(kept)


def merge_index() -> list[dict]:
    rows: list[dict] = []
    for f in sorted(IDX_DIR.glob("p*.json")):
        rows.extend(json.loads(f.read_text(encoding="utf-8")))
    rows.sort(key=lambda r: (r["page"], r["t"]))
    save_json(MANIFEST, rows)
    return rows


def main(argv: list[str]) -> int:
    qn = QN
    if "--qn" in argv:
        qn = int(argv[argv.index("--qn") + 1])
    pages = [int(a) for a in argv if a.isdigit()]
    videos = {int(v["page"]): v for v in
              json.loads((ROOT / "data" / "videos.json").read_text(encoding="utf-8"))["videos"]}
    wins = candidate_windows()
    FRAMES.mkdir(parents=True, exist_ok=True)
    IDX_DIR.mkdir(parents=True, exist_ok=True)
    DONE_DIR.mkdir(parents=True, exist_ok=True)

    todo = [p for p in (pages or sorted(videos))
            if p in wins and not (DONE_DIR / f"p{p:02d}").exists()]
    if not todo:
        rows = merge_index()
        print(f"没有待处理的集；manifest 共 {len(rows)} 帧")
        return 0

    print(f"待处理 {len(todo)} 集，qn={qn}，步长 {STEP} 帧，每章上限 {CHAPTER_CAP}", flush=True)
    ck = B.bili_cookies()
    mixin = B.wbi_keys(ck)
    total = 0
    for page in todo:
        try:
            total += process_page(page, videos[page], wins[page], ck, mixin, qn=qn)
        except Exception as e:  # noqa: BLE001
            print(f"  [{page:02d}] 失败 {type(e).__name__}: {e}", flush=True)
    rows = merge_index()
    print(f"完成：本次保留 {total} 帧；manifest 共 {len(rows)} 帧")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
