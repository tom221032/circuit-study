#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""vf_setup.py —— 抽帧分析工具链自检。

检查：ffmpeg（imageio-ffmpeg 自带）、opencv、numpy、OCR（rapidocr）、磁盘剩余、
以及本机临时工作目录 _frames/ 是否可写。

用法：
    python scripts/vf_setup.py
"""
from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FRAMES = ROOT / "_frames"


def ok(msg: str) -> None:
    print(f"[OK]   {msg}")


def bad(msg: str) -> None:
    print(f"[FAIL] {msg}")


def warn(msg: str) -> None:
    print(f"[WARN] {msg}")


def check_ffmpeg() -> bool:
    try:
        import imageio_ffmpeg

        exe = imageio_ffmpeg.get_ffmpeg_exe()
        if not Path(exe).exists():
            bad(f"imageio-ffmpeg 报告的 ffmpeg 不存在：{exe}")
            return False
        ok(f"ffmpeg {imageio_ffmpeg.get_ffmpeg_version()} @ {exe}")
        return True
    except Exception as e:  # noqa: BLE001
        bad(f"ffmpeg 不可用：{type(e).__name__}: {e}")
        return False


def check_cv() -> bool:
    try:
        import cv2
        import numpy as np

        ok(f"opencv {cv2.__version__} / numpy {np.__version__}")
        return True
    except Exception as e:  # noqa: BLE001
        bad(f"opencv/numpy 不可用：{type(e).__name__}: {e}")
        return False


def check_ocr() -> bool:
    try:
        from rapidocr_onnxruntime import RapidOCR
    except Exception as e:  # noqa: BLE001
        bad(f"rapidocr_onnxruntime 不可用：{type(e).__name__}: {e}")
        return False

    img = ROOT / "data" / "img" / "p6_0.png"
    if not img.exists():
        warn(f"缺少 OCR 测试图 {img}，只做导入检查")
        return True
    try:
        engine = RapidOCR()
        res, _ = engine(str(img))
        n = len(res or [])
        ok(f"OCR 可用，测试图识别出 {n} 行")
        if n:
            print("        样例：" + " | ".join(str(r[1])[:24] for r in (res or [])[:3]))
        return n > 0
    except Exception as e:  # noqa: BLE001
        bad(f"OCR 运行失败：{type(e).__name__}: {e}")
        return False


def check_disk() -> bool:
    total, used, free = shutil.disk_usage(str(ROOT))
    gb = free / 1024**3
    if gb < 10:
        bad(f"磁盘剩余 {gb:.1f} GB，不足 10 GB")
        return False
    ok(f"磁盘剩余 {gb:.1f} GB")
    return True


def check_dirs() -> bool:
    try:
        for d in ("playurl", "clips", "frames", "ocr"):
            (FRAMES / d).mkdir(parents=True, exist_ok=True)
        probe = FRAMES / ".write-test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        ok(f"工作目录可写：{FRAMES}")
    except Exception as e:  # noqa: BLE001
        bad(f"工作目录不可写：{type(e).__name__}: {e}")
        return False
    size = sum(p.stat().st_size for p in (ROOT / "data").rglob("*") if p.is_file())
    ok(f"现有 data/ 体积 {size/1024/1024:.1f} MB")
    return check_disk()


def main() -> int:
    print(f"python {sys.version.split()[0]}  cwd={os.getcwd()}")
    print(f"站点根：{ROOT}\n")
    results = [check_ffmpeg(), check_cv(), check_ocr(), check_dirs()]
    print()
    if all(results):
        print("全部通过，可以开始抽帧流程。")
        return 0
    print("有检查项未通过，见上面 [FAIL]。")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
