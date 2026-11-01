"""只读体检：检查 chapters 内容里插图与题注的配对质量。

用法:
    python scripts/check_images.py            # 打印报告
    python scripts/check_images.py --json     # 输出 JSON
"""

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"

FIG_RE = re.compile(r'<figure class="blk-fig" id="([^"]*)">(.*?)<figcaption>(.*?)</figcaption></figure>', re.S)
IMGONLY_RE = re.compile(r'<figure class="blk-img" id="([^"]*)">', re.S)
SRC_RE = re.compile(r'src="([^"]+)"')


def section_htmls(ch):
    for sec in ch["sections"]:
        if sec["kind"] == "notes":
            yield sec.get("html") or ""
        elif sec["kind"] == "ex":
            for it in sec.get("items") or []:
                yield it.get("q") or ""
                yield it.get("a") or ""
        else:
            for g in sec.get("groups") or []:
                yield g.get("html") or ""


def report():
    figs = []
    loose = []
    for f in sorted(DATA.glob("content/ch*.json")):
        ch = json.loads(f.read_text(encoding="utf-8"))
        for html in section_htmls(ch):
            for fid, body, cap in FIG_RE.findall(html):
                figs.append({"ch": ch["n"], "id": fid, "cap": cap,
                             "imgs": SRC_RE.findall(body)})
            for fid in IMGONLY_RE.findall(html):
                loose.append({"ch": ch["n"], "id": fid})

    no_img = [x for x in figs if not x["imgs"]]
    multi = [x for x in figs if len(x["imgs"]) > 1]
    refs = {p for x in figs for p in x["imgs"]}
    disk = {f"data/img/{p.name}" for p in (DATA / "img").glob("*")}
    return {
        "captions": len(figs),
        "captions_without_image": len(no_img),
        "captions_multi_image": len(multi),
        "loose_images": len(loose),
        "loose_pages": sorted({x["id"].split("_")[0][1:] for x in loose})[:40],
        "images_referenced": len(refs),
        "images_on_disk": len(disk),
        "missing_files": sorted(refs - disk)[:20],
        "unused_files": len(disk - refs),
        "samples_no_image": [{"ch": x["ch"], "cap": x["cap"]} for x in no_img[:10]],
    }


def main():
    r = report()
    if "--json" in sys.argv:
        print(json.dumps(r, ensure_ascii=False, indent=1))
        return
    print(f"题注总数            {r['captions']}")
    print(f"没有图的题注        {r['captions_without_image']}")
    print(f"挂多张图的题注      {r['captions_multi_image']}")
    print(f"散图（无题注）      {r['loose_images']}")
    print(f"引用图片文件        {r['images_referenced']} / 磁盘 {r['images_on_disk']}")
    print(f"缺文件              {len(r['missing_files'])}")
    print(f"未被引用的文件      {r['unused_files']}")
    if r["samples_no_image"]:
        print("样例（无图题注）:")
        for s in r["samples_no_image"]:
            print(f"  ch{s['ch']:02d}  {s['cap']}")


if __name__ == "__main__":
    main()
