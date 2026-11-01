# -*- coding: utf-8 -*-
"""从教材笔记 PDF 抽取 18 章的结构、正文、题目、插图 -> data/course.json + data/content/*.json + data/img/*"""
import html
import json
import hashlib
import re
import sys
from pathlib import Path

from pypdf import PdfReader

PDF = Path(r"C:\HONOR Share\Honor Share\邱关源《电路》（第6版）笔记和课后习题（含考研真题）详解.pdf")
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data"
IMG = OUT / "img"

CH_RE = re.compile(r"^第\s*(\d+)\s*章\s*(\S.*)$")
SEC_RE = re.compile(r"^(\d+)\.(\d)\s*　?\s*(复习笔记|课后习题详解|名校考研真题详解)")
H1_RE = re.compile(r"^[一二三四五六七八九十]+、\s*\S")
H2_RE = re.compile(r"^(\d{1,2})\s+(\S.{0,28})$")
FIG_RE = re.compile(r"^图\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*(.*)$")
TAB_RE = re.compile(r"^表\s*(\d+)\s*-\s*(\d+)\s*-\s*(\d+)\s*(.*)$")
ITEM_RE = re.compile(r"^(\d+)\s*-\s*(\d+)\s+")
EXAM_ITEM_RE = re.compile(r"^(\d{1,2})\s+(?=[图如已知求设在某将电一]|\S{0,0})")
SOL_RE = re.compile(r"^(解[:：]|【解】|\[解\])")

KIND = {"复习笔记": "notes", "课后习题详解": "ex", "名校考研真题详解": "exam"}

CJK = r"\u4e00-\u9fff"
PUNCT_CLOSE = "，。、；：？！）】》”’·"
PUNCT_OPEN = "（【《“‘"


def clean(s: str) -> str:
    """PDF 抽取常在中文字间夹空格，按中文排版规则收拢"""
    if not s:
        return s
    s = s.replace("\u3000", " ")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(rf"\s*([{PUNCT_CLOSE}])", r"\1", s)
    s = re.sub(rf"([{PUNCT_OPEN}])\s*", r"\1", s)
    s = re.sub(rf"(?<=[{CJK}])\s+(?=[{CJK}])", "", s)
    s = re.sub(r"\s+(?=\d)", " ", s)
    return s.strip()


def esc(s):
    return html.escape(clean(s), quote=False)


def join_lines(a: str, b: str) -> str:
    if not a:
        return b
    if not b:
        return a
    if a[-1].isascii() and a[-1].isalnum() and b[0].isascii() and b[0].isalnum():
        return a + " " + b
    return a + b


class Page:
    __slots__ = ("no", "lines", "images", "lines_y", "text_at", "img_boxes")

    def __init__(self, no, lines, images):
        self.no, self.lines, self.images = no, lines, images
        self.lines_y = [None] * len(lines)
        self.text_at = []
        self.img_boxes = []


CTM_TOKEN = re.compile(
    rb"q|Q|((?:[-\d.]+ +){5}[-\d.]+) +cm|/([^\s/\[\]()<>{}]+) +Do")


def _mul(m, n):
    a, b, c, d, e, f = m
    A, B, C, D, E, F = n
    return [a * A + c * B, b * A + d * B,
            a * C + c * D, b * C + d * D,
            a * E + c * F + e, b * E + d * F + f]


def _pt(m, x, y):
    a, b, c, d, e, f = m
    return (a * x + c * y + e, b * x + d * y + f)


def _hash_bytes(b):
    return hashlib.sha1(bytes(b)).hexdigest()


def _xobj(resources, name):
    try:
        res = resources.get_object() if hasattr(resources, "get_object") else resources
        xo = res.get("/XObject")
        if xo is None:
            return None
        xo = xo.get_object() if hasattr(xo, "get_object") else xo
        ref = xo.get("/" + name)
        return ref.get_object() if hasattr(ref, "get_object") else ref
    except Exception:
        return None


def image_boxes(page):
    """扫描内容流，返回每张图的 bbox 及它在 page.images 里的下标 k。

    阅读顺序 = y 递减；Form XObject 会递归进去；内联图片（BI…EI）忽略。
    """
    try:
        page_data = page.get_contents().get_data()
    except Exception:
        return []
    try:
        imgs = list(page.images)
    except Exception:
        imgs = []
    n2k = {}
    for k, im in enumerate(imgs):
        try:
            n2k.setdefault(Path(im.name).stem, k)
        except Exception:
            pass

    out = []
    seen = set()

    def scan(data, resources, base):
        stack = []
        ctm = list(base)
        for m in CTM_TOKEN.finditer(data):
            tok = m.group(0)
            if tok == b"q":
                stack.append(list(ctm))
            elif tok == b"Q":
                ctm = stack.pop() if stack else list(base)
            elif m.group(1):
                try:
                    ctm = _mul(ctm, [float(x) for x in m.group(1).split()])
                except ValueError:
                    continue
            elif m.group(2):
                name = m.group(2).decode("latin-1")
                obj = _xobj(resources, name)
                if obj is None:
                    continue
                if obj.get("/Subtype") == "/Form":
                    mat = obj.get("/Matrix") or [1, 0, 0, 1, 0, 0]
                    try:
                        m2 = [float(x) for x in mat]
                    except Exception:
                        m2 = [1, 0, 0, 1, 0, 0]
                    inner = obj.get("/Resources") or resources
                    try:
                        sdata = obj.get_data()
                    except Exception:
                        continue
                    scan(sdata, inner, _mul(ctm, m2))
                    continue
                k = n2k.get(name)
                if k is None or k in seen:
                    continue
                seen.add(k)
                pts = [_pt(ctm, x, y) for x, y in ((0, 0), (1, 0), (0, 1), (1, 1))]
                xs = [p[0] for p in pts]
                ys = [p[1] for p in pts]
                out.append({"k": k, "name": name, "x0": min(xs), "x1": max(xs),
                            "y0": min(ys), "y1": max(ys)})

    try:
        scan(page_data, page.get("/Resources"), [1, 0, 0, 1, 0, 0])
    except Exception:
        return []
    return out


def text_positions(page):
    """[(y, x, text)]，与 image_boxes 同一坐标空间；阅读顺序 = y 递减。"""
    out = []

    def visit(text, cm, tm, font_dict, font_size):
        if not text or not text.strip():
            return
        x = tm[4] * cm[0] + tm[5] * cm[2] + cm[4]
        y = tm[4] * cm[1] + tm[5] * cm[3] + cm[5]
        out.append((y, x, text))

    try:
        page.extract_text(visitor_text=visit)
    except Exception:
        pass
    return out


def _align_line_y(lines, text_at):
    """把 extract_text() 的行与逐块坐标对齐：返回每行的 y（对不上给 None）。"""
    if not text_at:
        return [None] * len(lines)
    bands = []
    for y, x, t in sorted(text_at, key=lambda c: (-c[0], c[1])):
        if bands and abs(bands[-1][0] - y) <= 3:
            bands[-1][1].append((x, t))
        else:
            bands.append([y, [(x, t)]])
    band_text = [re.sub(r"\s+", "", "".join(t for _, t in sorted(items))) for y, items in bands]
    ys, pos = [], 0
    for line in lines:
        key = re.sub(r"\s+", "", line)[:10]
        hit = None
        for k in range(pos, min(pos + 6, len(bands))):
            if key and key in band_text[k]:
                hit = k
                break
        if hit is None:
            # 「图 1-1-2」这类题注的「图/表」常被拆成单独 chunk，去掉首字再找；
            # 还不行就用尾部 6 字再找一次。都从当前位置往后找，保证阅读顺序单调。
            loose = key[1:] if len(key) > 4 else ""
            tail = key[-6:] if len(key) > 6 else ""
            for probe in (loose, tail):
                if not probe:
                    continue
                for k in list(range(pos, len(bands))) + list(range(0, pos)):
                    if probe in band_text[k]:
                        hit = k
                        break
                if hit is not None:
                    break
        if hit is None:
            ys.append(bands[pos][0] if pos < len(bands) else None)
        else:
            ys.append(bands[hit][0])
            pos = hit + 1
    return ys


def read_pages(reader):
    pages = []
    total = len(reader.pages)
    for i in range(total):
        p = reader.pages[i]
        try:
            text = p.extract_text() or ""
        except Exception:
            text = ""
        lines = [ln.strip() for ln in text.split("\n")]
        lines = [ln for ln in lines if ln not in ("", "\u3000")]
        imgs = []
        try:
            for im in p.images:
                imgs.append((im.name, im.data))
        except Exception:
            pass
        pg = Page(i + 1, lines, imgs)
        try:
            pg.text_at = text_positions(p)
            pg.img_boxes = image_boxes(p)
        except Exception:
            pg.text_at, pg.img_boxes = [], []
        pg.lines_y = _align_line_y(lines, pg.text_at)
        pages.append(pg)
        if (i + 1) % 50 == 0:
            print(f"  read {i + 1}/{total}", flush=True)
    return pages


def save_images(pages):
    IMG.mkdir(parents=True, exist_ok=True)
    out = {}
    for pg in pages:
        refs = []
        for k, (name, data) in enumerate(pg.images):
            ext = Path(name).suffix.lower()
            if ext not in (".png", ".jpg", ".jpeg", ".gif", ".bmp"):
                ext = ".png"
            fn = f"p{pg.no}_{k}{ext}"
            (IMG / fn).write_bytes(data)
            refs.append(f"data/img/{fn}")
        out[pg.no] = refs
    return out


def looks_like_caption(line):
    """图/表标题：短、不以句号结尾、不是「……所示」这类正文片段"""
    t = line.strip()
    return len(t) <= 30 and not t.endswith(("。", "；")) and "所示" not in t


def classify(line):
    if CH_RE.match(line) or SEC_RE.match(line):
        return "skip"
    if H1_RE.match(line):
        return "h1"
    if TAB_RE.match(line) and looks_like_caption(line):
        return "tab"
    if FIG_RE.match(line) and looks_like_caption(line):
        return "fig"
    if "＝" in line or "=" in line:
        return "formula" if len(line) < 46 else "para"
    if SOL_RE.match(line) or line.startswith("【答案】") or line.startswith("【解析】"):
        return "mark"
    if H2_RE.match(line):
        m = H2_RE.match(line)
        rest = m.group(2)
        if len(rest) <= 26 and not rest.endswith(("。", "，", "、")) and "：" not in rest:
            return "h2"
    return "para"


def build_blocks(pages, start, end):
    """把 [start,end] 页拼成块序列，块带 page / type / y（y 为版面上的位置）"""
    blocks = []
    cur = None

    def flush():
        nonlocal cur
        if cur:
            blocks.append(cur)
            cur = None

    for pno in range(start, end + 1):
        pg = pages[pno - 1]
        for li, line in enumerate(pg.lines):
            ly = pg.lines_y[li] if li < len(pg.lines_y) else None
            kind = classify(line)
            if kind == "skip":
                flush()
                continue
            if kind == "para":
                if cur and cur["type"] == "para" and not cur["text"].endswith(("。", "！", "？", "：", "；")):
                    cur["text"] = join_lines(cur["text"], line)
                    cur["page"] = pno
                else:
                    flush()
                    cur = {"type": "para", "text": line, "page": pno, "y": ly}
                continue
            flush()
            blocks.append({"type": kind, "text": line, "page": pno, "y": ly})
    flush()
    return blocks


def block_html(b, fig_img=None):
    t = b["text"]
    if b["type"] == "h1":
        return f'<h2 class="blk-h1" id="{b["id"]}">{esc(t)}</h2>'
    if b["type"] == "h2":
        return f'<h3 class="blk-h2" id="{b["id"]}">{esc(t)}</h3>'
    if b["type"] == "formula":
        return f'<p class="blk-formula">{esc(t)}</p>'
    if b["type"] in ("fig", "tab"):
        paths = b.get("imgs_paths")
        if paths is None:
            paths = [fig_img] if fig_img else []
        imgs = "".join(f'<img loading="lazy" src="{p}" alt="{esc(t)}">' for p in paths if p)
        return (f'<figure class="blk-fig" id="{b["id"]}">{imgs}'
                f'<figcaption>{esc(t)}</figcaption></figure>')
    if b["type"] == "imgonly":
        return (f'<figure class="blk-img" id="{b["id"]}">'
                f'<img loading="lazy" src="{fig_img}" alt="原书插图"></figure>')
    if b["type"] == "mark":
        return f'<p class="blk-mark">{esc(t)}</p>'
    return f'<p class="blk-p" id="{b["id"]}">{esc(t)}</p>'


def _ans_details(body_html):
    return (f'<details class="ans"><summary>显示答案与解析</summary>'
            f'<div class="ans-body"><p>{body_html}</p></div></details>')


def exam_group_html(blocks):
    """考研真题：题干留下，答案/解析折进 details，方便先自己动手做"""
    out = []  # [(kind, payload)] kind: p / raw / details
    for b in blocks:
        t = b["text"]
        if t.strip() in ("【解析】", "【答案】"):
            continue  # 孤立的标记块
        if b["type"] in ("h1", "h2", "fig", "tab", "imgonly"):
            out.append(("raw", block_html(b, b.get("img"))))
            continue
        if "【答案】" not in t and "【解析】" in t:
            # 「RAB＝…【解析】」这种标记倒装在句尾的续写行，整段都属于解析
            content = t.replace("【解析】", "").strip()
            if content:
                piece = f'<b class="ans-k">解析：</b>{esc(content)}'
                if out and out[-1][0] == "details":
                    out[-1] = ("details", out[-1][1] + "<br>" + piece)
                else:
                    out.append(("details", piece))
            continue
        i = t.find("【答案】")
        if i < 0:
            i = t.find("【解析】")
        if i < 0:
            mo = re.match(r"^[A-D]\s*[．.]", t)
            m = re.search(r"([A-D])\s*$", t)
            if mo and m and m.start(1) > 3:  # 「A．1V B．－1.5V … D．－1V B」末尾那个字母就是答案
                out.append(("p", t[:m.start(1)].strip()))
                out.append(("details", f'<b class="ans-k">答案：</b>{esc(m.group(1))}'))
                continue
            if re.match(r"^(解|答|分析)\s*[：:]", t):
                # 「解：…」整段是解答，藏进答案里
                body = f'<b class="ans-k">解析：</b>{esc(re.sub(r"^(解|答|分析)\\s*[：:]\\s*", "", t))}'
                if out and out[-1][0] == "details":
                    out[-1] = ("details", out[-1][1] + "<br>" + body)
                else:
                    out.append(("details", body))
                continue
            if out and out[-1][0] == "details" and "＝" in t and not re.match(r"^\d", t):
                # 紧跟在答案后面的推导行（「回路中的电流为 I＝…」），继续收进同一个答案框
                out[-1] = ("details", out[-1][1] + "<br>" + esc(t))
                continue
            out.append(("raw", block_html(b, b.get("img"))))
            continue
        head, tail = t[:i].strip(), t[i:]
        m = re.search(r"研\s*\]", head)
        if m and len(head) - m.end() < 24:  # 「…[ 某校 2023 研 ]除源后的入端；开路」这类答案值也要藏
            tail = head[m.end():] + tail
            head = head[:m.end()].strip()
        def fmt_tail(tail_text, have_answer=False):
            prefix = ""
            if not tail_text.startswith(("【答案】", "【解析】")):
                marks = [p for p in (tail_text.find("【答案】"), tail_text.find("【解析】")) if p >= 0]
                j = min(marks) if marks else len(tail_text)
                if 0 < j <= 40:  # 标记前的这段就是答案值（「对」「除源后的入端；开路」等）
                    prefix = f'<b class="ans-k">答案：</b>{esc(tail_text[:j])}<br>'
                    tail_text = tail_text[j:]
                    have_answer = True
                    if tail_text and not tail_text.startswith("【解析】"):
                        prefix += '<b class="ans-k">解析：</b>'
            if have_answer and tail_text.startswith("【答案】"):
                tail_text = tail_text[len("【答案】"):]
            return prefix + esc(tail_text).replace("【答案】", '<b class="ans-k">答案：</b>').replace(
                "【解析】", '<br><b class="ans-k">解析：</b>')

        body = fmt_tail(tail)
        mo = re.match(r"^[A-D]\s*[．.]", head)
        m2 = re.search(r"([A-D])\s*$", head)
        if mo and m2 and m2.start(1) > 3:  # 选项行末尾的字母就是答案
            body = f'<b class="ans-k">答案：</b>{esc(m2.group(1))}<br>{fmt_tail(tail, True)}'
            head = head[:m2.start(1)].strip()
        if len(head) <= 12:  # 只剩「1.1」这种答案值，也藏起来
            if head:
                body = f'<b class="ans-k">答案：</b>{esc(head)}<br>{fmt_tail(tail, True)}'
        elif head:
            out.append(("p", head))
        out.append(("details", body))
    html = []
    for kind, payload in out:
        if kind == "p":
            html.append(f'<p class="blk-p">{esc(payload)}</p>')
        elif kind == "details":
            html.append(_ans_details(payload))
        else:
            html.append(payload)
    return "".join(html)


def exam_block_html(b, fig_img=None):
    """单个块的版本（保留给其它调用点）"""
    return exam_group_html([b])


CAP_GAP = 80.0      # 题注与图片边缘的最大竖直距离
MULTI_GAP = 25.0    # 同一个题注可吞下的相邻图之间的最大竖直距离
MAX_PER_CAP = 8     # 一个题注最多挂几张图（整张表常被切成多张）


def _img_name_map(pg):
    """XObject 名 -> page.images 下标"""
    m = {}
    for k, (nm, _data) in enumerate(pg.images):
        m[Path(nm).stem] = k
    return m


def _page_boxes(pages, pno, imgmap):
    """[(k, box)]，按阅读顺序（y 递减）排好，重复绘制的图只留第一次"""
    if not (1 <= pno <= len(pages)):
        return []
    seen = set()
    out = []
    for bx in pages[pno - 1].img_boxes:
        k = bx.get("k")
        if k is None or k in seen:
            continue
        seen.add(k)
        out.append((k, bx))
    out.sort(key=lambda t: -t[1]["y0"])
    return out


def _path(imgmap, pno, k):
    try:
        return imgmap[pno][k]
    except Exception:
        return None


def _x_overlap(cap, bx):
    cx0, cx1 = cap.get("x0"), cap.get("x1")
    if cx0 is None or cx1 is None:
        return True
    return not (cx1 < bx["x0"] - 10 or cx0 > bx["x1"] + 10)


def assign_images(blocks, pages, imgmap, start, end):
    """按版面几何把插图配到 图/表 题注上；配不上的图按阅读位置插回行间。"""
    caps = [b for b in blocks if b["type"] in ("fig", "tab")]
    for b in caps:
        b["imgs"] = []

    boxes_by_page = {pno: _page_boxes(pages, pno, imgmap)
                     for pno in range(start, end + 1)}
    used = set()

    # 1) 同页配对：题注与其紧邻的图，按距离从小到大贪心
    cand = []
    for ci, cap in enumerate(caps):
        cy = cap.get("y")
        if cy is None:
            continue
        for pno in (cap["page"],):
            for k, bx in boxes_by_page.get(pno, []):
                if not _x_overlap(cap, bx):
                    continue
                dist = min(abs(cy - bx["y0"]), abs(cy - bx["y1"]))
                if dist <= CAP_GAP:
                    cand.append((round(dist, 1), ci, pno, k))
    cand.sort(key=lambda t: (t[0], t[1], t[2], t[3]))
    for dist, ci, pno, k in cand:
        cap = caps[ci]
        if (pno, k) in used or len(cap["imgs"]) >= MAX_PER_CAP:
            continue
        used.add((pno, k))
        cap["imgs"].append({"page": pno, "k": k, "path": _path(imgmap, pno, k), "dist": dist})

    # 2) 跨页只允许「上一页最后一张图 <-> 本页第一个题注」
    for ci, cap in enumerate(caps):
        if cap["imgs"] or cap.get("y") is None:
            continue
        pno = cap["page"]
        prev = boxes_by_page.get(pno - 1, [])
        if not prev:
            continue
        k, bx = prev[-1]
        if (pno - 1, k) in used:
            continue
        dist = abs(cap["y"] - bx["y0"])
        if dist <= CAP_GAP and _x_overlap(cap, bx):
            used.add((pno - 1, k))
            cap["imgs"].append({"page": pno - 1, "k": k,
                                "path": _path(imgmap, pno - 1, k), "dist": round(dist, 1)})

    # 3) 一个题注可以吞下紧邻的 (a)(b) 分图
    for cap in caps:
        if len(cap["imgs"]) != 1:
            continue
        cy = cap.get("y")
        if cy is None:
            continue
        pno = cap["page"]
        boxes = boxes_by_page.get(pno, [])
        if not boxes:
            continue
        anchor = cap["imgs"][0]
        idx = next((i for i, (k, bx) in enumerate(boxes) if k == anchor["k"]), None)
        if idx is None:
            continue
        for step in (-1, 1):
            j = idx + step
            while 0 <= j < len(boxes) and len(cap["imgs"]) < MAX_PER_CAP:
                k, bx = boxes[j]
                if (pno, k) in used:
                    break
                prev_bx = boxes[j - step][1]
                gap = abs(bx["y0"] - prev_bx["y1"]) if step < 0 else abs(prev_bx["y0"] - bx["y1"])
                if gap > MULTI_GAP:
                    break
                w1 = prev_bx["x1"] - prev_bx["x0"]
                ov = min(bx["x1"], prev_bx["x1"]) - max(bx["x0"], prev_bx["x0"])
                if w1 <= 0 or ov / w1 < 0.5:
                    break
                used.add((pno, k))
                cap["imgs"].append({"page": pno, "k": k,
                                    "path": _path(imgmap, pno, k), "dist": 0})
                j += step
        y_of = {k: bx["y0"] for k, bx in boxes}
        cap["imgs"].sort(key=lambda d: -y_of.get(d["k"], 0))

    # 3b) 兜底：仍然没图的题注，取同页（再邻页）最近的未用图，放宽距离与横向约束
    for cap in caps:
        if cap["imgs"] or cap.get("y") is None:
            continue
        cy = cap["y"]
        best = None
        for pno in (cap["page"], cap["page"] - 1, cap["page"] + 1):
            for k, bx in boxes_by_page.get(pno, []):
                if (pno, k) in used:
                    continue
                d = min(abs(cy - bx["y0"]), abs(cy - bx["y1"]))
                if abs(pno - cap["page"]) > 0:
                    d += 40  # 邻页优先度更低
                if best is None or d < best[0]:
                    best = (d, pno, k, bx)
        if best and best[0] <= 300:
            d, pno, k, bx = best
            used.add((pno, k))
            cap["imgs"].append({"page": pno, "k": k,
                                "path": _path(imgmap, pno, k), "dist": round(d, 1)})

    for b in caps:
        b["img"] = b["imgs"][0]["path"] if b["imgs"] else None
        b["imgs_paths"] = [d["path"] for d in b["imgs"] if d["path"]]

    # 4) 剩下的图按阅读位置插回块序列（y 递减 = 越往后）
    out = list(blocks)
    for pno in range(end, start - 1, -1):
        loose = [(k, bx) for k, bx in boxes_by_page.get(pno, []) if (pno, k) not in used]
        for k, bx in loose:
            path = _path(imgmap, pno, k)
            if not path:
                continue
            y = bx["y0"]
            idxs = [i for i, b in enumerate(out) if b["page"] == pno]
            at = None
            for i in idxs:
                by = out[i].get("y")
                if by is not None and by < y:
                    at = i
                    break
            if at is None:
                at = (idxs[-1] + 1) if idxs else len(out)
            out.insert(at, {"type": "imgonly", "page": pno, "img": path,
                            "id": f"x{pno}_{k}", "text": "", "y": y})
    return out


def make_toc(blocks):
    toc = []
    for b in blocks:
        if b["type"] == "h1":
            toc.append({"level": 1, "text": b["text"], "anchor": b["id"]})
        elif b["type"] == "h2":
            toc.append({"level": 2, "text": b["text"], "anchor": b["id"]})
    return toc


def split_items(blocks, item_re, chapter):
    items, cur = [], None
    for b in blocks:
        m = item_re.match(b["text"]) if b["type"] == "para" else None
        if m:
            label = f"{m.group(1)}-{m.group(2)}" if item_re is ITEM_RE else m.group(1)
            first = {"type": "para", "text": b["text"][m.end():].strip(), "page": b["page"], "id": b["id"]}
            cur = {"no": label, "blocks": [first]}
            items.append(cur)
        elif cur:
            cur["blocks"].append(b)
    return items


def build_chapter(ch, title, page, secs, pages, imgmap):
    sections = []
    for si, (kind, spage, next_page) in enumerate(secs):
        end = next_page - 1 if next_page else len(pages)
        blocks = build_blocks(pages, spage, end)
        for i, b in enumerate(blocks):
            b["id"] = f"c{ch}s{si}b{i}"
        blocks = assign_images(blocks, pages, imgmap, spage, end)

        sec = {"key": f"{ch}.{['1','2','3'][si]}", "kind": kind, "page": spage, "endPage": end}
        if kind == "notes":
            sec["toc"] = make_toc(blocks)
            sec["html"] = "".join(block_html(b, b.get("img")) for b in blocks)
        elif kind == "ex":
            items = split_items(blocks, ITEM_RE, ch)
            out = []
            for it in items:
                q, a = [], []
                state = "q"
                for b in it["blocks"]:
                    if state == "q" and (SOL_RE.match(b["text"]) or b["text"].startswith("【解】")):
                        state = "a"
                    (a if state == "a" else q).append(b)
                if not a:  # 没有「解」标记，就整段当题目
                    q, a = it["blocks"], []
                out.append({
                    "no": it["no"],
                    "q": "".join(block_html(b, b.get("img")) for b in q),
                    "a": "".join(block_html(b, b.get("img")) for b in a),
                })
            sec["items"] = out
        else:
            groups, cur = [], None
            for b in blocks:
                if b["type"] == "h1":
                    cur = {"title": b["text"], "blocks": []}
                    groups.append(cur)
                else:
                    if cur is None:
                        cur = {"title": "名校考研真题", "blocks": []}
                        groups.append(cur)
                    cur["blocks"].append(b)
            for g in groups:
                g["html"] = exam_group_html(g["blocks"])
                del g["blocks"]
            sec["groups"] = groups
        sections.append(sec)
    return {"n": ch, "title": title, "page": page, "sections": sections}


def main():
    print("reading pdf ...", flush=True)
    reader = PdfReader(str(PDF))
    pages = read_pages(reader)
    print("saving images ...", flush=True)
    imgmap = save_images(pages)

    chapters = []
    for pno in range(5, len(pages) + 1):
        for line in pages[pno - 1].lines:
            m = CH_RE.match(line)
            if m:
                chapters.append((int(m.group(1)), m.group(2).strip(), pno))
                break
    print("chapters:", [(c[0], c[2]) for c in chapters])

    secs_by_ch = {}
    for pno in range(5, len(pages) + 1):
        for line in pages[pno - 1].lines:
            m = SEC_RE.match(line)
            if m:
                secs_by_ch.setdefault(int(m.group(1)), {})[int(m.group(2))] = (KIND[m.group(3)], pno)

    OUT.mkdir(exist_ok=True)
    (OUT / "content").mkdir(exist_ok=True)
    course = {"pdf": str(PDF), "pages": len(pages), "chapters": []}
    for i, (ch, title, page) in enumerate(chapters):
        nxt = chapters[i + 1][2] if i + 1 < len(chapters) else len(pages) + 1
        raw = secs_by_ch.get(ch, {})
        order = [raw.get(1), raw.get(2), raw.get(3)]
        secs = []
        for si, item in enumerate(order):
            if not item:
                continue
            later = [raw[k][1] for k in (si + 2, si + 3) if k in raw]
            secs.append((item[0], item[1], later[0] if later else (nxt if si == 2 or ch != chapters[-1][0] else len(pages) + 1)))
        # 章节末节不跨章
        for si in range(len(secs)):
            if si + 1 < len(secs):
                secs[si] = (secs[si][0], secs[si][1], secs[si + 1][1])
            else:
                secs[si] = (secs[si][0], secs[si][1], nxt)
        data = build_chapter(ch, title, page, secs, pages, imgmap)
        (OUT / "content" / f"ch{ch:02d}.json").write_text(
            json.dumps(data, ensure_ascii=False), encoding="utf-8")
        course["chapters"].append({
            "n": ch, "title": title, "page": page,
            "sections": [{"key": s["key"], "kind": s["kind"], "page": s["page"], "endPage": s["endPage"],
                          **({"toc": s["toc"]} if "toc" in s else {}),
                          "items": len(s.get("items", []))} for s in data["sections"]],
        })
        print(f"  chapter {ch}: {title}  pages {page}-{nxt-1}  sections={len(secs)}", flush=True)
    (OUT / "course.json").write_text(json.dumps(course, ensure_ascii=False, indent=1), encoding="utf-8")
    print("done")


if __name__ == "__main__":
    main()
