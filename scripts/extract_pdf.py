# -*- coding: utf-8 -*-
"""从教材笔记 PDF 抽取 18 章的结构、正文、题目、插图 -> data/course.json + data/content/*.json + data/img/*"""
import html
import json
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
    __slots__ = ("no", "lines", "images")

    def __init__(self, no, lines, images):
        self.no, self.lines, self.images = no, lines, images


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
        pages.append(Page(i + 1, lines, imgs))
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
    """把 [start,end] 页拼成块序列，块带 page 与 type"""
    blocks = []
    cur = None

    def flush():
        nonlocal cur
        if cur:
            blocks.append(cur)
            cur = None

    for pno in range(start, end + 1):
        pg = pages[pno - 1]
        for line in pg.lines:
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
                    cur = {"type": "para", "text": line, "page": pno}
                continue
            flush()
            blocks.append({"type": kind, "text": line, "page": pno})
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
        img = f'<img loading="lazy" src="{fig_img}" alt="{esc(t)}">' if fig_img else ""
        return (f'<figure class="blk-fig" id="{b["id"]}">{img}'
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


def assign_images(blocks, imgmap, start, end):
    """按阅读顺序把插图配到图/表标题上；配不上的图作为独立块插回所在页末尾"""
    used = set()
    all_imgs = []
    for pno in range(start, end + 1):
        for k, path in enumerate(imgmap.get(pno, [])):
            all_imgs.append((pno, k, path))

    def take(cap_page):
        for i, (pno, k, path) in enumerate(all_imgs):
            if i in used:
                continue
            if pno == cap_page:
                used.add(i)
                return path
        return None

    caps = [b for b in blocks if b["type"] in ("fig", "tab")]
    for cap in caps:
        cap["img"] = take(cap["page"])
        if cap["img"] is None:  # 同页没有多余图，借用最近的页
            best, bestd = None, 99
            for i, (pno, k, path) in enumerate(all_imgs):
                if i in used:
                    continue
                d = abs(pno - cap["page"])
                if d < bestd:
                    best, bestd = i, d
            if best is not None and bestd <= 2:
                used.add(best)
                cap["img"] = all_imgs[best][2]

    loose = {}
    for i, (pno, k, path) in enumerate(all_imgs):
        if i not in used:
            loose.setdefault(pno, []).append(path)
    out = list(blocks)
    for pno in sorted(loose):
        paths = loose[pno]
        idxs = [i for i, b in enumerate(out) if b["page"] == pno]
        at = (idxs[-1] + 1) if idxs else len(out)
        new = [{"type": "imgonly", "page": pno, "img": p, "text": "", "id": f"x{pno}_{j}"}
               for j, p in enumerate(paths)]
        out[at:at] = new
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
        blocks = assign_images(blocks, imgmap, spage, end)

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
