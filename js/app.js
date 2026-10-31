// 电路学习站 —— 纯前端，数据全部来自 data/
import { renderMathIn } from './math.js';

const $ = (s, r = document) => r.querySelector(s);
const $$ = (s, r = document) => Array.from(r.querySelectorAll(s));

const S = {
  course: null, videos: null, subsIndex: {}, search: null,
  store: JSON.parse(localStorage.getItem('circuit-study-v1') || '{}'),
};
S.store.watched ||= {};
S.store.bookmarks ||= {};
S.store.notes ||= {};
S.store.edits ||= {};
S.store.last ||= null;

const cache = new Map();
async function getJSON(path) {
  if (cache.has(path)) return cache.get(path);
  const p = fetch(path).then(r => { if (!r.ok) throw new Error(path + ' ' + r.status); return r.json(); });
  cache.set(path, p);
  return p;
}
function save() { localStorage.setItem('circuit-study-v1', JSON.stringify(S.store)); }

/* ---------------- 编辑原文（调试功能） ----------------
   - 顶栏 ✎ 开关：打开后可点正文任意段落直接改（编辑期间不跑 KaTeX，显示原始文本）
   - 撤销 / 恢复各 15 步：一次连续输入算一步（停手 450ms 或失焦时提交）
   - 「复原全部」清空所有改动并重绘，同时清空撤销/恢复栈，避免两者互相干扰
   ------------------------------------------------------ */
const EDIT_LIMIT = 15;
const EDIT_SEL = '.blk-p, .blk-mark, .blk-h1, .blk-h2, .blk-formula, figcaption, .kp-title, .kp-t, .ans-body p';
let editUndo = [], editRedo = [], editOrig = new Map(), editBase = new Map();
let editTimers = new Map(), editSaveT = 0;

function editableUnder(root) {
  const all = $$(EDIT_SEL, root);
  return all.filter(el => !all.some(o => o !== el && o.contains(el)));
}
function stampEdits(root, base) {
  const nodes = editableUnder(root);
  editOrig = new Map();
  editBase = new Map();
  nodes.forEach((el, i) => {
    const uid = `${base}:${el.id || 'i' + i}`;
    el.dataset.uid = uid;
    editOrig.set(uid, el.innerHTML);
    const saved = S.store.edits[uid];
    if (saved != null && saved !== el.innerHTML) el.innerHTML = saved;
    el.classList.toggle('edited', saved != null);
    editBase.set(uid, el.innerHTML);
  });
  return nodes;
}
function editSaveSoon() {
  clearTimeout(editSaveT);
  editSaveT = setTimeout(save, 400);
}
function editSyncBar() {
  const bar = $('#editBar');
  if (!bar) return;
  bar.hidden = !S.editMode;
  $('#edUndoN').textContent = editUndo.length;
  $('#edRedoN').textContent = editRedo.length;
  $('#edUndo').disabled = !editUndo.length;
  $('#edRedo').disabled = !editRedo.length;
}
function editCommitEl(el) {
  const uid = el.dataset.uid;
  if (!uid) return;
  const t = editTimers.get(el);
  if (t) { clearTimeout(t); editTimers.delete(el); }
  const before = editBase.get(uid);
  const after = el.innerHTML;
  if (before == null || before === after) return;
  editBase.set(uid, after);
  const changed = after !== editOrig.get(uid);
  el.classList.toggle('edited', changed);
  if (!changed) delete S.store.edits[uid];
  else S.store.edits[uid] = after;
  save();
  editUndo.push({ uid, before, after });
  if (editUndo.length > EDIT_LIMIT) editUndo.shift();
  editRedo = [];
  editSyncBar();
}
function editFlushAll() {
  [...editTimers.keys()].forEach(el => editCommitEl(el));
}
function editWrite(uid, html) {
  if (html === editOrig.get(uid)) delete S.store.edits[uid];
  else S.store.edits[uid] = html;
  editBase.set(uid, html);
  save();
  const orig = editOrig.get(uid);
  $$('[data-uid]').forEach(el => {
    if (el.dataset.uid !== uid) return;
    if (el.innerHTML !== html) el.innerHTML = html;
    el.classList.toggle('edited', html !== orig);
  });
}
function editUndoStep() {
  editFlushAll();
  const e = editUndo.pop();
  if (!e) { toast('没有可撤销的修改了'); return false; }
  editWrite(e.uid, e.before);
  editRedo.push(e);
  if (editRedo.length > EDIT_LIMIT) editRedo.shift();
  editSyncBar();
  return true;
}
function editRedoStep() {
  editFlushAll();
  const e = editRedo.pop();
  if (!e) { toast('没有可恢复的修改了'); return false; }
  editWrite(e.uid, e.after);
  editUndo.push(e);
  if (editUndo.length > EDIT_LIMIT) editUndo.shift();
  editSyncBar();
  return true;
}
function editResetAll() {
  editTimers.forEach(t => clearTimeout(t));
  editTimers.clear();
  S.store.edits = {};
  save();
  editUndo = []; editRedo = [];
  editBase.clear();
  editSyncBar();
  const y = window.scrollY;
  Promise.resolve(route()).then(() => requestAnimationFrame(() => window.scrollTo(0, y)));
  toast('已复原全部修改，撤销记录一并清空');
}
function makeEditable(nodes) {
  nodes.forEach(el => {
    el.setAttribute('contenteditable', 'true');
    el.setAttribute('spellcheck', 'false');
    el.classList.add('editable');
    el.addEventListener('input', () => {
      const uid = el.dataset.uid;
      if (!uid) return;
      const changed = el.innerHTML !== editOrig.get(uid);
      el.classList.toggle('edited', changed);
      if (!changed) delete S.store.edits[uid];
      else S.store.edits[uid] = el.innerHTML;
      editSaveSoon();
      const t = editTimers.get(el);
      if (t) clearTimeout(t);
      editTimers.set(el, setTimeout(() => editCommitEl(el), 450));
    });
    el.addEventListener('blur', () => editCommitEl(el));
    el.addEventListener('keydown', e => {
      // 段落内换行用 <br>，避免 contenteditable 自己套 <div>
      if (e.key === 'Enter' && !e.isComposing) {
        e.preventDefault();
        document.execCommand('insertLineBreak');
      }
    });
  });
}
function setEditMode(on) {
  S.editMode = !!on;
  document.body.classList.toggle('editing', S.editMode);
  const btn = $('#btnEdit');
  if (btn) btn.setAttribute('aria-pressed', String(S.editMode));
  editSyncBar();
  const y = window.scrollY;
  Promise.resolve(route()).then(() => requestAnimationFrame(() => window.scrollTo(0, y)));
  toast(S.editMode ? '编辑模式：点任意段落直接改；Esc 退出' : '已退出编辑，公式恢复排版');
}

const pad2 = n => String(n).padStart(2, '0');
function fmtDur(sec) {
  sec = Math.round(sec);
  const h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60);
  return h ? `${h}:${pad2(m)}:${pad2(sec % 60)}` : `${m}:${pad2(sec % 60)}`;
}
function fmtLong(sec) { return sec >= 3600 ? `${(sec / 3600).toFixed(1)} 小时` : `${Math.round(sec / 60)} 分钟`; }
function esc(s) { return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c])); }
function hl(text, q) {
  const t = esc(text);
  if (!q) return t;
  const i = t.toLowerCase().indexOf(q.toLowerCase());
  if (i < 0) return t;
  return t.slice(0, i) + '<mark>' + t.slice(i, i + q.length) + '</mark>' + t.slice(i + q.length);
}

let toastTimer;
function toast(msg) {
  const t = $('#toast');
  t.textContent = msg;
  t.classList.add('show');
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => t.classList.remove('show'), 1800);
}

const chapterVideos = n => S.videos.videos.filter(v => v.chapter === n);
const videoByPage = p => S.videos.videos[p - 1];
const hasSub = v => !S.subs || !!S.subs[String(v.page)];
// 原书 PDF 可能不存在（例如部署到 GitHub Pages 时为了版权/体积没有上传），探测后再决定是否显示链接
const bookLink = (page, label, cls = 'pdf-link') =>
  S.hasBook ? `<a class="${cls}" href="books/notes.pdf#page=${page}" target="_blank" rel="noopener">${label} ↗</a>` : '';
function watchedCount() { return Object.keys(S.store.watched).length; }

// ---------------- sidebar ----------------
function renderNav() {
  const { parts } = parseHash();
  const curCh = parts[0] === 'chapter' || parts[0] === 'video'
    ? (parts[0] === 'chapter' ? Number(parts[1]) : videoByPage(Number(parts[1]))?.chapter)
    : null;
  const curPage = parts[0] === 'video' ? Number(parts[1]) : null;

  $('#chapterNav').innerHTML = S.course.chapters.map(ch => {
    const vs = chapterVideos(ch.n);
    const done = vs.filter(v => S.store.watched[v.page]).length;
    const open = ch.n === curCh;
    const sub = open ? `<div class="nav-sub">${vs.map(v =>
      `<a href="#/video/${v.page}" class="${S.store.watched[v.page] ? 'done' : ''}${v.page === curPage ? ' active' : ''}">
        <span>${esc(v.title.replace(/^第.+?章\s*/, '').slice(0, 22) || v.title)}</span>
        <span>${fmtDur(v.duration)}${hasSub(v) ? '' : ' <span class="nosub">无字幕</span>'}</span></a>`).join('')}</div>` : '';
    return `<a class="nav-ch ${open ? 'active' : ''}" href="#/chapter/${ch.n}">
        <span class="n">${pad2(ch.n)}</span>${esc(ch.title)}${done ? ` <span class="n">${done}/${vs.length}</span>` : ''}</a>${sub}`;
  }).join('');

  const pct = Math.round(watchedCount() / S.videos.total * 100);
  $('#pmText').textContent = pct + '%';
  $('#pmBar').style.width = pct + '%';
}

// ---------------- router ----------------
function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, qs] = raw.split('?');
  return { parts: path.split('/').filter(Boolean), q: new URLSearchParams(qs || '') };
}

async function route() {
  const { parts, q } = parseHash();
  const view = $('#view');
  setSidebar(false);
  renderNav();
  window.scrollTo({ top: 0 });
  try {
    if (!parts.length) { await viewHome(view); return; }
    if (parts[0] === 'chapter') { await viewChapter(view, Number(parts[1]), q); return; }
    if (parts[0] === 'video') { await viewVideo(view, Number(parts[1]), q); return; }
    if (parts[0] === 'search') { await viewSearch(view, q); return; }
    view.innerHTML = '<div class="empty">页面不存在 · <a href="#/">回到首页</a></div>';
  } catch (e) {
    console.error(e);
    view.innerHTML = `<div class="empty">加载失败：${esc(e.message)}<br><br>如果是 file:// 打开，请用 start.cmd 启动本地服务。</div>`;
  }
}

// ---------------- home ----------------
async function viewHome(view) {
  document.title = '电路 · 邱关源第六版 学习站';
  $('#crumbs').innerHTML = '<b>课程首页</b>';
  const pct = Math.round(watchedCount() / S.videos.total * 100);
  const last = S.store.last ? videoByPage(S.store.last) : null;

  view.innerHTML = `
    <section class="hero">
      <h1>${esc(S.videos.title.replace(/【.*?】/g, '').trim())}</h1>
      <div class="sub">${esc(S.videos.owner)} 主讲 · 邱关源《电路》第六版 · ${S.videos.total} 集全集修复版</div>
      <div class="hero-stats">
        <div class="hstat"><b>18</b><span>章</span></div>
        <div class="hstat"><b>${S.videos.total}</b><span>集视频</span></div>
        <div class="hstat"><b>${(S.videos.totalDuration / 3600).toFixed(0)}</b><span>小时课程</span></div>
        <div class="hstat"><b>${S.course.kpTotal || 0}</b><span>条视频知识点</span></div>
        <div class="hstat"><b>${pct}%</b><span>已看进度</span></div>
      </div>
      <div class="hero-actions">
        <a class="btn primary" href="${last ? `#/video/${last.page}` : '#/video/1'}">${last ? `继续学习 · 第 ${last.page} 集` : '从第 1 集开始'}</a>
        <a class="btn" href="#/chapter/1">按章学习</a>
        <a class="btn" href="#/search">搜索字幕与笔记</a>
      </div>
    </section>

    ${last ? `<div class="section-title"><h2>继续上次</h2></div>
      <a class="resume" href="#/video/${last.page}">
        <span class="dot">▶</span>
        <span class="txt"><b>第 ${last.page} 集 · ${esc(last.title)}</b>
        <span>${esc(last.chapterName || '')} · 上次看到这里</span></span>
        <span class="btn">继续</span>
      </a>` : ''}

    <div class="section-title"><h2>十八讲</h2><span>石群 · 邱关源《电路》第六版 · 每章含复习笔记 / 课后习题 / 考研真题</span></div>
    <div class="grid">
      ${S.course.chapters.map(ch => {
        const vs = chapterVideos(ch.n);
        const done = vs.filter(v => S.store.watched[v.page]).length;
        const p = vs.length ? Math.round(done / vs.length * 100) : 0;
        const mins = vs.reduce((a, v) => a + v.duration, 0) / 60;
        return `<a class="card" href="#/chapter/${ch.n}">
          <div class="cn">CHAPTER ${pad2(ch.n)}</div>
          <h3>${esc(ch.title)}</h3>
          <div class="meta"><span>${vs.length} 集</span><span>${fmtLong(mins * 60)}</span>${ch.kp ? `<span>${ch.kp.count} 知识点</span>` : ''}<span>p.${ch.page}</span><span>${p}%</span></div>
          <div class="bar"><i style="width:${p}%"></i></div>
        </a>`;
      }).join('')}
    </div>`;
}

// ---------------- 外观：主题 / 字号 ----------------
const THEME_META = { light: '#f6f5f1', dark: '#14181d' };
function applyTheme(t) {
  if (t === 'dark') document.documentElement.dataset.theme = 'dark';
  else { delete document.documentElement.dataset.theme; t = 'light'; }
  S.store.theme = t; save();
  const btn = $('#btnTheme');
  if (btn) { btn.textContent = t === 'dark' ? '☀' : '◐'; btn.title = t === 'dark' ? '切到浅色模式' : '切到暗色模式'; }
  const meta = document.querySelector('meta[name="theme-color"]');
  if (meta) meta.content = THEME_META[t];
}
const FS_STEPS = [0.8, 0.9, 1, 1.15, 1.3, 1.45, 1.6, 1.75];
function applyFs(v) {
  v = Math.max(FS_STEPS[0], Math.min(FS_STEPS[FS_STEPS.length - 1], Number(v) || 1));
  document.documentElement.style.setProperty('--fs', String(v));
  S.store.fs = v; save();
}
function stepFs(dir) {
  const cur = Number(S.store.fs || 1);
  let i = FS_STEPS.findIndex(x => Math.abs(x - cur) < 0.001);
  if (i < 0) {
    // 记录里的值不在档位内（比如旧版本存的 1.08）：取最接近的档
    let best = 0;
    FS_STEPS.forEach((x, k) => { if (Math.abs(x - cur) < Math.abs(FS_STEPS[best] - cur)) best = k; });
    i = best;
  }
  const next = i + dir;
  if (next < 0) return toast('已经是最小字号');
  if (next >= FS_STEPS.length) return toast('已经是最大字号');
  applyFs(FS_STEPS[next]);
}

// ---------------- 侧栏 / 灯箱 ----------------
function setSidebar(open) {
  $('#sidebar').classList.toggle('open', open);
  const scrim = $('#scrim');
  if (scrim) scrim.hidden = !open;
  document.body.style.overflow = open ? 'hidden' : '';
}
function openLightbox(url) {
  const lb = $('#lightbox');
  if (!lb) return;
  $('img', lb).src = url;
  lb.hidden = false;
  document.body.style.overflow = 'hidden';
}
function closeLightbox() {
  const lb = $('#lightbox');
  if (!lb || lb.hidden) return;
  lb.hidden = true;
  $('img', lb).removeAttribute('src');
  if (!$('#sidebar').classList.contains('open')) document.body.style.overflow = '';
}

// ---------------- 笔记图片（IndexedDB） ----------------
let imgDBPromise = null;
function imgDB() {
  if (!imgDBPromise) {
    imgDBPromise = new Promise((res, rej) => {
      if (!('indexedDB' in window)) { rej(new Error('no indexedDB')); return; }
      const req = indexedDB.open('circuit-notes', 1);
      req.onupgradeneeded = () => {
        const db = req.result;
        if (!db.objectStoreNames.contains('imgs')) {
          const st = db.createObjectStore('imgs', { keyPath: 'id', autoIncrement: true });
          st.createIndex('note', 'note', { unique: false });
        }
      };
      req.onsuccess = () => res(req.result);
      req.onerror = () => rej(req.error);
    });
  }
  return imgDBPromise;
}
function idb(mode, fn) {
  return imgDB().then(db => new Promise((res, rej) => {
    const tx = db.transaction('imgs', mode);
    let out;
    const r = fn(tx.objectStore('imgs'));
    if (r) r.onsuccess = () => { out = r.result; };
    tx.oncomplete = () => res(out);
    tx.onerror = () => rej(tx.error);
    tx.onabort = () => rej(tx.error || new Error('aborted'));
  }));
}
const noteImgs = note => idb('readonly', st => st.index('note').getAll(note));
const noteImgAdd = rec => idb('readwrite', st => st.add(rec));
const noteImgDel = id => idb('readwrite', st => st.delete(id));

// 压缩后用 JPEG 存库，避免 localStorage 5MB 上限
async function shrinkImage(file, maxSide = 1500, quality = 0.85) {
  // 不同来源的图片（截图/相册/某些 PNG 编码）不一定能被 createImageBitmap 解开，
  // 这里退一步用 <img> 解码，再不行就原样存文件，保证「一定能插进去」。
  let src = null;
  try {
    src = await createImageBitmap(file);
  } catch (e) {
    src = null;
  }
  let url = '';
  if (!src) {
    url = URL.createObjectURL(file);
    try {
      src = await new Promise((res, rej) => {
        const im = new Image();
        im.onload = () => res(im);
        im.onerror = () => rej(new Error('decode failed'));
        im.src = url;
      });
    } catch (e) {
      URL.revokeObjectURL(url);
      return { blob: file, w: 0, h: 0, type: file.type || 'image/png' };
    }
  }
  const sw = src.width || src.naturalWidth || 0;
  const sh = src.height || src.naturalHeight || 0;
  if (!sw || !sh) {
    if (url) URL.revokeObjectURL(url);
    if (src.close) src.close();
    return { blob: file, w: 0, h: 0, type: file.type || 'image/png' };
  }
  const scale = Math.min(1, maxSide / Math.max(sw, sh));
  const w = Math.max(1, Math.round(sw * scale));
  const h = Math.max(1, Math.round(sh * scale));
  const cv = document.createElement('canvas');
  cv.width = w; cv.height = h;
  const g = cv.getContext('2d');
  g.fillStyle = '#fff'; g.fillRect(0, 0, w, h);
  g.drawImage(src, 0, 0, w, h);
  if (src.close) src.close();
  if (url) URL.revokeObjectURL(url);
  const blob = await new Promise(r => cv.toBlob(r, 'image/jpeg', quality));
  if (!blob) return { blob: file, w: sw, h: sh, type: file.type || 'image/png' };
  return { blob, w, h, type: 'image/jpeg' };
}

// 同一张图片在多次渲染间复用 objectURL，避免重复创建
const imgUrlCache = new Map();
function imgUrlFor(id, blob) {
  let u = imgUrlCache.get(id);
  if (!u) { u = URL.createObjectURL(blob); imgUrlCache.set(id, u); }
  return u;
}

// 把一张图包成「不可拆分」的行内块，图片就夹在文字中间
function nimgWrap(id, blob) {
  const span = document.createElement('span');
  span.className = 'nimg';
  span.setAttribute('contenteditable', 'false');
  span.dataset.img = String(id);
  const img = document.createElement('img');
  img.src = imgUrlFor(id, blob);
  img.alt = '笔记图片';
  span.appendChild(img);
  const del = document.createElement('button');
  del.type = 'button';
  del.className = 'nimg-del';
  del.title = '删除这张图';
  del.textContent = '✕';
  span.appendChild(del);
  return span;
}

// 存盘时把行内图块降级成 <img src="noteimg:ID">，正文文字原样保留
function serializeNote(el) {
  const clone = el.cloneNode(true);
  clone.querySelectorAll('.nimg').forEach(w => {
    const id = w.dataset.img;
    const img = document.createElement('img');
    img.setAttribute('data-img', id);
    img.setAttribute('src', `noteimg:${id}`);
    w.replaceWith(img);
  });
  clone.querySelectorAll('br[data-ph]').forEach(b => b.remove());
  return clone.innerHTML;
}

async function initNoteEditor(n) {
  const key = 'ch' + n;
  const el = $('#myNote');
  if (!el) return;
  const box = $('#noteEditor');
  const fileInput = $('#noteFile');

  const markDirty = () => {
    S.store.notes[key] = serializeNote(el);
    save();
  };

  // ---- 载入：正文 + 行内图片 ----
  let rows = [];
  try { rows = (await noteImgs(key)) || []; } catch (e) {
    rows = [];
  }
  // 丢掉空 blob（历史失败插入留下的坏记录），避免出现破图
  const bad = rows.filter(r => !r.blob || !r.blob.size);
  if (bad.length) {
    bad.forEach(r => noteImgDel(r.id).catch(() => {}));
    rows = rows.filter(r => r.blob && r.blob.size);
  }
  const byId = new Map(rows.map(r => [r.id, r]));
  const raw = (S.store.notes && S.store.notes[key]) || '';

  if (/<(img|br|div|span|p)\b/i.test(raw)) {
    // 先去掉 noteimg: 占位 src，避免浏览器把它当成真请求（控制台会报 ERR_UNKNOWN_URL_SCHEME）
    el.innerHTML = raw.replace(/(<img[^>]*?)\ssrc="noteimg:[^"]*"/g, '$1');
  } else {
    el.textContent = raw; // 老版本纯文本笔记
  }

  const used = new Set();
  [...el.querySelectorAll('img')].forEach(img => {
    const id = Number(img.dataset.img || String(img.getAttribute('src') || '').replace('noteimg:', ''));
    const rec = byId.get(id);
    if (!rec) { img.remove(); return; }
    used.add(id);
    img.replaceWith(nimgWrap(id, rec.blob));
  });

  // 老版本里「单独列出」的图片没有进正文，这里补到笔记末尾，避免丢图。
  // 只有当正文里一张图都没有（= 纯老格式）时才补，避免已被删掉的图又冒出来。
  const orphans = used.size ? [] : rows.filter(r => !used.has(r.id));
  if (orphans.length) {
    if (el.textContent && !/\n$/.test(el.textContent)) el.appendChild(document.createElement('br'));
    orphans.forEach(r => {
      el.appendChild(nimgWrap(r.id, r.blob));
      el.appendChild(document.createElement('br'));
      used.add(r.id);
    });
    markDirty();
  }

  // ---- 存盘 + 清理不再引用的图片 ----
  let timer = 0;
  const scheduleSave = () => {
    clearTimeout(timer);
    timer = setTimeout(async () => {
      markDirty();
      const keep = new Set([...el.querySelectorAll('.nimg')].map(w => Number(w.dataset.img)));
      rows.forEach(r => { if (!keep.has(r.id)) noteImgDel(r.id).catch(() => {}); });
    }, 260);
  };
  el.addEventListener('input', scheduleSave);
  el.addEventListener('blur', () => { clearTimeout(timer); markDirty(); });

  // 回车统一插 <br>（默认会插 <div>，多次回车会嵌套出越来越深的 div）
  el.addEventListener('keydown', e => {
    if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {
      e.preventDefault();
      document.execCommand('insertLineBreak');
      scheduleSave();
    }
  });

  // ---- 在光标处插入 ----
  const caretInside = () => {
    const s = window.getSelection();
    return s && s.rangeCount && el.contains(s.getRangeAt(0).commonAncestorContainer);
  };
  function insertNode(node, withBreak = false) {
    const sel = window.getSelection();
    el.focus();
    if (caretInside()) {
      const r = sel.getRangeAt(0);
      r.deleteContents();
      r.insertNode(node);
      let last = node;
      if (withBreak) {
        const br = document.createElement('br');
        node.parentNode.insertBefore(br, node.nextSibling);
        last = br;
      }
      const after = document.createRange();
      after.setStartAfter(last);
      after.collapse(true);
      sel.removeAllRanges();
      sel.addRange(after);
    } else {
      el.appendChild(node);
      if (withBreak) el.appendChild(document.createElement('br'));
    }
    markDirty();
  }

  // ---- 图片入库并在光标处插入 ----
  async function addFiles(files, atDropPoint) {
    const list = Array.from(files || []).filter(f => f && f.size > 0 && String(f.type).startsWith('image/'));
    if (!list.length) { toast('没读到图片文件（可能已失效，请重新选择）'); return; }
    let ok = 0;
    for (const f of list) {
      try {
        const { blob, w, h, type } = await shrinkImage(f);
        const id = await noteImgAdd({ note: key, blob, w, h, type, name: f.name || '截图', ts: Date.now() });
        const node = nimgWrap(id, blob);
        rows.push({ id, note: key, blob, w, h, type });
        insertNode(node, true);
        ok++;
      } catch (e) { console.error(e); }
    }
    if (ok) toast(`已插入 ${ok} 张图片`);
    else toast('图片处理失败');
  }

  // 按钮选图
  const btn = $('#noteImgBtn');
  if (btn) btn.addEventListener('click', () => fileInput && fileInput.click());
  if (fileInput) fileInput.addEventListener('change', () => {
    addFiles(fileInput.files);
    fileInput.value = '';
  });

  // 粘贴：有图就插图，纯文本就按光标插入
  el.addEventListener('paste', e => {
    const dt = e.clipboardData;
    if (!dt) return;
    const files = Array.from(dt.files || []);
    if (files.length) { e.preventDefault(); addFiles(files); return; }
    const text = dt.getData('text/plain');
    if (text) {
      e.preventDefault();
      document.execCommand('insertText', false, text);
    }
  });

  // 拖拽：插到落点位置
  if (box) {
    ['dragenter', 'dragover'].forEach(t => box.addEventListener(t, e => {
      if (e.dataTransfer && Array.from(e.dataTransfer.types || []).includes('Files')) {
        e.preventDefault(); box.classList.add('note-drop');
      }
    }));
    ['dragleave', 'dragend'].forEach(t => box.addEventListener(t, () => box.classList.remove('note-drop')));
    box.addEventListener('drop', e => {
      box.classList.remove('note-drop');
      if (e.dataTransfer && e.dataTransfer.files && e.dataTransfer.files.length) {
        e.preventDefault();
        const r = document.caretRangeFromPoint ? document.caretRangeFromPoint(e.clientX, e.clientY) : null;
        if (r && el.contains(r.startContainer)) {
          el.focus();
          const sel = window.getSelection();
          sel.removeAllRanges();
          sel.addRange(r);
        }
        addFiles(e.dataTransfer.files);
      }
    });
  }

  // 点击缩略图看大图 / 点 ✕ 删图
  el.addEventListener('click', e => {
    const del = e.target.closest && e.target.closest('.nimg-del');
    if (del) {
      e.preventDefault();
      const wrap = del.closest('.nimg');
      if (!wrap) return;
      const id = Number(wrap.dataset.img);
      wrap.remove();
      rows = rows.filter(r => r.id !== id);
      noteImgDel(id).catch(() => {});
      markDirty();
      toast('已删除图片');
      return;
    }
    const img = e.target.closest && e.target.closest('.nimg img');
    if (img) openLightbox(img.src);
  });

  // 图片上按 Backspace/Delete 直接删整块
  el.addEventListener('keydown', e => {
    if (e.key !== 'Backspace' && e.key !== 'Delete') return;
    const sel = window.getSelection();
    if (!sel || !sel.rangeCount || !sel.isCollapsed) return;
    let node = sel.getRangeAt(0).startContainer;
    let wrap = node.nodeType === 1 ? node.closest('.nimg') : node.parentElement && node.parentElement.closest('.nimg');
    if (wrap) {
      e.preventDefault();
      const id = Number(wrap.dataset.img);
      wrap.remove();
      rows = rows.filter(r => r.id !== id);
      noteImgDel(id).catch(() => {});
      markDirty();
    }
  });
}

// ---------------- chapter ----------------
async function viewChapter(view, n, q) {
  const ch = S.course.chapters[n - 1];
  if (!ch) { view.innerHTML = '<div class="empty">没有这一章</div>'; return; }
  const data = await getJSON(`data/content/ch${pad2(n)}.json`);
  document.title = `第${n}章 ${ch.title} · 电路`;
  $('#crumbs').innerHTML = `<a href="#/">首页</a><span class="sep">/</span><b>第 ${n} 章 ${esc(ch.title)}</b>`;

  const vs = chapterVideos(n);
  const done = vs.filter(v => S.store.watched[v.page]).length;
  const wantTab = q.get('tab');
  const firstKind = data.sections[0].kind;
  const activeKind = wantTab || firstKind;

  const tabs = data.sections.map(s => {
    const label = s.kind === 'notes'
      ? `复习笔记${ch.kp?.count ? `<span class="kp-badge">+${ch.kp.count}</span>` : ''}`
      : s.kind === 'ex' ? `课后习题 <span style="opacity:.6">${s.items?.length || 0}</span>`
      : '考研真题';
    return `<button class="tab ${s.kind === activeKind ? 'active' : ''}" data-kind="${s.kind}">${label}</button>`;
  }).join('');

  view.innerHTML = `
    <header class="ch-head">
      <div>
        <div class="num">CHAPTER ${pad2(n)} · PDF p.${ch.page}</div>
        <h1>${esc(ch.title)}</h1>
        <div class="meta">
          <span>${vs.length} 集视频 · ${fmtLong(vs.reduce((a, v) => a + v.duration, 0))}</span>
          <span>已看 ${done}/${vs.length}</span>
          ${bookLink(ch.page, `对照原书 p.${ch.page}`)}
        </div>
      </div>
      <div class="spacer"></div>
      <a class="btn" href="#/video/${vs[0]?.page || 1}">▶ 看本章第 1 集</a>
    </header>

    <div class="ch-layout">
      <div>
        <div class="tabs">${tabs}</div>
        <div class="reader" id="reader"></div>
        <div class="card note-card" id="noteCard" style="margin-top:16px">
          <details>
            <summary>✎ 我的本章笔记（自动保存在本机，可粘贴图片）</summary>
            <div class="note-editor" id="noteEditor">
              <div class="rich" id="myNote" contenteditable="true" spellcheck="false" data-ph="在这里写自己的推导、易错点…… 截图可以直接 Ctrl+V 粘贴，也可以拖进来；图片会插在光标所在的位置。"></div>
              <div class="note-tools">
                <button class="btn ghost" id="noteImgBtn" type="button">🖼 在光标处插入图片</button>
                <span class="hint">粘贴截图 / 拖入图片 / 选文件都行，图片按插入位置夹在文字中间；点大图可预览，点图片右上角 ✕ 删除</span>
              </div>
              <input type="file" id="noteFile" accept="image/*" multiple hidden>
            </div>
          </details>
        </div>
      </div>

      <aside class="side-panel">
        <h3>本章视频 · ${vs.length} 集</h3>
        <div class="vlist-videos">
          ${vs.map(v => `<a class="vitem ${S.store.watched[v.page] ? 'done' : ''}" href="#/video/${v.page}">
            <span class="idx">${pad2(v.page)}</span>
            <span class="t">${esc(v.title)}${hasSub(v) ? '' : ' <span class="nosub">无字幕</span>'}</span>
            <span class="dur">${fmtDur(v.duration)}</span></a>`).join('')}
        </div>
      </aside>
    </div>`;

  const reader = $('#reader');
  initNoteEditor(n);

  function paint(kind) {
    const sec = data.sections.find(s => s.kind === kind);
    $$('.tab').forEach(t => t.classList.toggle('active', t.dataset.kind === kind));
    if (!sec) { reader.innerHTML = '<div class="empty">本节暂无内容</div>'; return; }

    if (kind === 'notes') {
      reader.innerHTML = `<div class="notice">本节整理自《邱关源电路第六版笔记和课后习题详解》p.${sec.page}–${sec.endPage}（文字与插图由 PDF 自动抽取，公式请以原书为准）。
        ${bookLink(sec.page, `打开原书 p.${sec.page}`)}</div>
        ${ch.kp?.count ? `<div class="kp-legend"><span>📺 本节含 ${ch.kp.count} 条视频知识点（源自石群讲稿，点时间可跳到视频）</span>
          <span style="flex:1"></span><button class="btn ghost" id="kpOnly">只看视频补充</button></div>` : ''}
        ${sec.toc?.length ? `<details style="margin-bottom:14px"><summary style="cursor:pointer;font-size:13px;color:var(--muted)">本章目录（${sec.toc.length} 节）</summary>
          <div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px">${sec.toc.map(t =>
            `<a class="chip" href="#${t.anchor}" data-anchor="${t.anchor}">${esc(t.text)}</a>`).join('')}</div></details>` : ''}
        ${sec.html}`;
      const onlyBtn = $('#kpOnly', reader);
      if (onlyBtn) onlyBtn.addEventListener('click', () => {
        const on = reader.classList.toggle('only-kp');
        onlyBtn.textContent = on ? '显示全部笔记' : '只看视频补充';
      });
      $$('.chip[data-anchor]', reader).forEach(a => a.addEventListener('click', e => {
        e.preventDefault();
        const el = document.getElementById(a.dataset.anchor);
        if (el) { el.scrollIntoView({ behavior: 'smooth', block: 'start' }); el.style.transition = 'background .6s'; el.style.background = '#fff6d8'; setTimeout(() => el.style.background = '', 1200); }
      }));
    } else if (kind === 'ex') {
      reader.innerHTML = `<div class="notice">来自原书 p.${sec.page}–${sec.endPage}，共 ${sec.items.length} 题。点「显示解答」展开。</div>
        <div class="ex-list">${sec.items.map((it, i) => `
          <div class="ex" data-i="${i}">
            <div class="ex-h">
              <span class="ex-no">${esc(it.no)}</span>
              <div class="ex-q">${it.q}</div>
              <button class="btn ghost ex-toggle">显示解答 ▾</button>
            </div>
            ${it.a ? `<div class="ex-a">${it.a}</div>` : ''}
          </div>`).join('')}</div>`;
      $$('.ex', reader).forEach(box => {
        const btn = $('.ex-toggle', box);
        if (!$('.ex-a', box)) { btn.style.display = 'none'; return; }
        btn.addEventListener('click', () => {
          const open = box.classList.toggle('open');
          btn.textContent = open ? '收起解答 ▴' : '显示解答 ▾';
        });
      });
    } else {
      reader.innerHTML = `<div class="notice">来自原书 p.${sec.page}–${sec.endPage}，按题型分组。</div>
        ${sec.groups.map(g => `<section style="margin-bottom:22px"><h2 class="blk-h1">${esc(g.title)}</h2>${g.html}</section>`).join('')}`;
    }
    const nodes = stampEdits(reader, `c${n}:${kind}`);
    if (S.editMode) makeEditable(nodes);
    else renderMathIn(reader);
  }
  $$('.tab').forEach(t => t.addEventListener('click', () => { paint(t.dataset.kind); history.replaceState(null, '', `#/chapter/${n}?tab=${t.dataset.kind}`); }));
  paint(activeKind);

  const anchor = q.get('anchor');
  if (anchor) {
    const el = document.getElementById(anchor);
    if (el) setTimeout(() => { el.scrollIntoView({ behavior: 'smooth', block: 'center' }); el.style.background = '#fff6d8'; setTimeout(() => el.style.background = '', 1600); }, 60);
  }
}

// ---------------- video ----------------
let transcriptState = null;
async function viewVideo(view, page, q) {
  const v = videoByPage(page);
  if (!v) { view.innerHTML = '<div class="empty">没有这一集</div>'; return; }
  document.title = `第${v.page}集 ${v.title} · 电路`;
  $('#crumbs').innerHTML = `<a href="#/">首页</a><span class="sep">/</span>
    <a href="#/chapter/${v.chapter}">第 ${v.chapter} 章 ${esc(v.chapterName || '')}</a><span class="sep">/</span><b>第 ${v.page} 集</b>`;

  S.store.last = v.page; save();
  renderNav();

  const markDone = !!S.store.watched[v.page];
  const marked = !!S.store.bookmarks[v.page];
  const ch = S.course.chapters[v.chapter - 1];
  const startT = Math.floor(Number(q.get('t') || 0));
  const embedSrc = startT
    ? `${v.embed.replace('autoplay=0', 'autoplay=1')}&t=${startT}`
    : v.embed;
  if (S.kpByVideo === undefined) {
    S.kpByVideo = await getJSON('data/kp/by-video.json').catch(() => null);
  }
  const kps = (S.kpByVideo || {})[String(v.page)] || [];
  const kpHtml = kps.length ? `<details class="card" style="margin-top:16px;cursor:default" open>
      <summary style="cursor:pointer;font-size:13.5px;color:var(--muted)">📺 本集讲到的 ${kps.length} 个知识点（点条目跳到笔记对应位置）</summary>
      <div style="margin-top:10px">${kps.map(k => `<a class="kp-mini" href="#/chapter/${k.c}?tab=notes&anchor=${k.anchor}">
        <span class="k">${esc(k.kind)}</span><span class="kp-mini-t">${esc(k.title)}</span>
        <span style="font-family:var(--mono);font-size:11px;color:#9aa3ad;flex:none">${fmtDur(k.t)}</span></a>`).join('')}</div></details>` : '';

  view.innerHTML = `
    <div class="v-layout">
      <div>
        <div class="player-wrap"><iframe id="player" src="${embedSrc}" allowfullscreen scrolling="no" frameborder="0" referrerpolicy="no-referrer"></iframe></div>
        <h1 class="v-title">${esc(v.title)}</h1>
        <div class="v-meta">
          <span>第 ${v.page} / ${S.videos.total} 集</span>
          <span>${fmtDur(v.duration)}</span>
          <span>第 ${v.chapter} 章 · ${esc(v.chapterName || '')}</span>
          ${hasSub(v) ? '' : '<span class="nosub">本集 B 站未提供 AI 字幕</span>'}
          <span style="font-family:var(--mono);color:#9aa3ad">${S.videos.bvid}-p${v.page}</span>
        </div>
        <div class="v-actions">
          <button class="btn ${markDone ? 'primary' : ''}" id="btnDone">${markDone ? '✓ 已看' : '标记为已看'}</button>
          <button class="btn ${marked ? 'primary' : ''}" id="btnMark">${marked ? '★ 已收藏' : '☆ 收藏'}</button>
          <a class="btn" href="#/chapter/${v.chapter}">📖 本章笔记</a>
        </div>
        <div class="v-links">
          ${ch ? bookLink(ch.page, `原书 p.${ch.page}`, 'btn ghost') : ''}
          <a class="btn ghost" href="${v.url}" target="_blank" rel="noopener">在 B 站打开 ↗</a>
          <a class="btn ghost" id="btnPrev" href="${v.page > 1 ? `#/video/${v.page - 1}` : '#/'}">← 上一集</a>
          <a class="btn ghost" id="btnNext" href="${v.page < S.videos.total ? `#/video/${v.page + 1}` : '#/'}">下一集 →</a>
        </div>
        ${kpHtml}
      </div>

      <aside class="transcript">
        <div class="tr-head">
          <b>AI 字幕</b><span class="count" id="trCount">…</span>
          <span style="flex:1"></span>
          <button class="btn ghost" id="trCopy" title="复制全文">⧉</button>
          <button class="btn ghost" id="trDl" title="下载 TXT">⤓</button>
          <input class="tr-search" id="trSearch" placeholder="在字幕里找……（回车跳到第一条）">
        </div>
        <div class="tr-body" id="trBody"><div class="tr-empty">字幕加载中…</div></div>
        <div class="tr-foot"><span>点句子：跳到该处播放</span><span id="trInfo"></span></div>
      </aside>
    </div>`;
  renderMathIn(view);

  $('#btnDone').addEventListener('click', e => {
    if (S.store.watched[v.page]) delete S.store.watched[v.page]; else S.store.watched[v.page] = Date.now();
    save(); renderNav();
    const b = e.currentTarget;
    b.textContent = S.store.watched[v.page] ? '✓ 已看' : '标记为已看';
    b.classList.toggle('primary', !!S.store.watched[v.page]);
  });
  $('#btnMark').addEventListener('click', e => {
    if (S.store.bookmarks[v.page]) delete S.store.bookmarks[v.page]; else S.store.bookmarks[v.page] = Date.now();
    save();
    const b = e.currentTarget;
    b.textContent = S.store.bookmarks[v.page] ? '★ 已收藏' : '☆ 收藏';
    b.classList.toggle('primary', !!S.store.bookmarks[v.page]);
  });

  // 键盘：左右切集
  const onKey = e => {
    if (['INPUT', 'TEXTAREA'].includes(e.target.tagName)) return;
    if (e.key === 'ArrowLeft' && v.page > 1) location.hash = `#/video/${v.page - 1}`;
    if (e.key === 'ArrowRight' && v.page < S.videos.total) location.hash = `#/video/${v.page + 1}`;
  };
  document.addEventListener('keydown', onKey);
  addEventListener('hashchange', () => document.removeEventListener('keydown', onKey), { once: true });

  await loadTranscript(v, q);
}

function seekTo(v, t) {
  const f = $('#player');
  if (!f) return;
  const base = v.embed.replace('autoplay=0', 'autoplay=1');
  f.src = `${base}&t=${Math.floor(t)}`;
}

async function loadTranscript(v, q) {
  const body = $('#trBody');
  const count = $('#trCount');
  let data = null;
  try { data = await getJSON(`data/subs/${v.cid}.json`); } catch (e) { data = null; }

  if (!data || !data.lines || !data.lines.length) {
    count.textContent = '无';
    $('#trInfo').textContent = '';
    body.innerHTML = `<div class="tr-empty">这一集没有抓取到 AI 字幕。<br><br>
      可能是原视频未生成 AI 字幕，或字幕尚未采集。<br>
      你仍然可以看视频 + 对照本章笔记。<br><br>
      <a class="btn" href="#/chapter/${v.chapter}">去看第 ${v.chapter} 章笔记</a></div>`;
    return;
  }

  const lines = data.lines;
  count.textContent = `${lines.length} 句`;
  $('#trInfo').textContent = data.lan ? `语言 ${data.lan}` : '';

  let filter = '';
  let shown = 0;
  const BATCH = 260;

  function lineNode(ln, i) {
    return `<div class="tr-line" data-i="${i}" data-t="${ln.t}">
      <span class="ts">${fmtDur(ln.t).padStart(5, '0')}</span>
      <span class="tx">${hl(ln.text, filter)}</span></div>`;
  }

  function render() {
    const list = filter ? lines.map((ln, i) => ({ ln, i })).filter(x => x.ln.text.toLowerCase().includes(filter)) : lines.map((ln, i) => ({ ln, i }));
    shown = Math.min(BATCH, list.length);
    body.innerHTML = list.length
      ? list.slice(0, shown).map(x => lineNode(x.ln, x.i)).join('')
      : '<div class="tr-empty">没有匹配的句子</div>';
    body.dataset.total = list.length;
    bind();
  }

  function bind() {
    $$('.tr-line', body).forEach(el => {
      el.addEventListener('click', () => {
        $$('.tr-line', body).forEach(x => x.classList.remove('active'));
        el.classList.add('active');
        seekTo(v, Number(el.dataset.t));
        toast(`跳到 ${fmtDur(Number(el.dataset.t))}`);
      });
    });
  }

  body.addEventListener('scroll', () => {
    const total = Number(body.dataset.total || 0);
    const list = filter ? lines.map((ln, i) => ({ ln, i })).filter(x => x.ln.text.toLowerCase().includes(filter)) : lines.map((ln, i) => ({ ln, i }));
    if (shown < total && body.scrollTop + body.clientHeight > body.scrollHeight - 400) {
      const next = Math.min(shown + BATCH, total);
      const frag = list.slice(shown, next).map(x => lineNode(x.ln, x.i)).join('');
      body.insertAdjacentHTML('beforeend', frag);
      shown = next;
      bind();
    }
  });

  let tmr;
  $('#trSearch').addEventListener('input', e => {
    clearTimeout(tmr);
    tmr = setTimeout(() => { filter = e.target.value.trim().toLowerCase(); body.scrollTop = 0; render(); }, 180);
  });
  $('#trCopy').addEventListener('click', () => {
    const txt = lines.map(l => `[${fmtDur(l.t)}] ${l.text}`).join('\n');
    navigator.clipboard.writeText(txt).then(() => toast('字幕全文已复制'), () => toast('复制失败'));
  });
  $('#trDl').addEventListener('click', () => {
    const txt = `第${v.page}集 ${v.title}\n${v.url}\n\n` + lines.map(l => `[${fmtDur(l.t)}] ${l.text}`).join('\n');
    const a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([txt], { type: 'text/plain;charset=utf-8' }));
    a.download = `字幕-第${v.page}集.txt`;
    a.click();
    URL.revokeObjectURL(a.href);
    toast('已导出字幕');
  });

  render();

  const t = Number(q.get('t') || 0);
  if (t) {
    const idx = lines.findIndex(l => l.t >= t);
    if (idx >= 0) {
      const el = $(`.tr-line[data-i="${idx}"]`, body);
      if (el) { el.classList.add('active'); el.scrollIntoView({ block: 'center' }); }
    }
  }
}

// ---------------- search ----------------
async function viewSearch(view, q) {
  document.title = '搜索 · 电路';
  $('#crumbs').innerHTML = '<a href="#/">首页</a><span class="sep">/</span><b>全站搜索</b>';
  const initial = q.get('q') || '';
  view.innerHTML = `
    <div class="search-hero">
      <h1>搜索笔记、知识点与字幕</h1>
      <input class="search-input" id="q" placeholder="例如：戴维宁定理 / 相量 / 三要素法 / RC 电路" value="${esc(initial)}">
      <div class="filters">
        <button class="chip on" data-k="all">全部</button>
        <button class="chip" data-k="note">笔记/习题</button>
        <button class="chip" data-k="kp">视频知识点</button>
        <button class="chip" data-k="sub">视频字幕</button>
      </div>
    </div>
    <div id="results" class="res-list"></div>`;
  const input = $('#q');
  let kind = 'all';
  input.focus();

  const results = $('#results');
  async function run() {
    const query = input.value.trim();
    if (query.length < 1) { results.innerHTML = '<div class="empty">输入关键词开始搜索</div>'; return; }
    results.innerHTML = '<div class="loading">搜索中…</div>';
    if (!S.search) {
      try { S.search = await getJSON('data/search.json'); } catch (e) { results.innerHTML = '<div class="empty">搜索索引还没生成</div>'; return; }
    }
    const ql = query.toLowerCase();
    const hits = [];
    for (const it of S.search.items) {
      if (kind !== 'all' && it.k !== kind) continue;
      if (!it.x.toLowerCase().includes(ql)) continue;
      hits.push(it);
      if (hits.length >= 120) break;
    }
    if (!hits.length) { results.innerHTML = `<div class="empty">没有找到「${esc(query)}」</div>`; return; }
    results.innerHTML = hits.map(it => {
      if (it.k === 'sub') {
        return `<a class="res" href="#/video/${it.v}?t=${it.t}">
          <div class="r-top"><span class="tag sub">字幕</span><span>第 ${it.v} 集</span><span>${fmtDur(it.t)}</span>
            <span class="r-go">跳到视频 →</span></div>
          <div class="r-text">${hl(it.x, query)}</div></a>`;
      }
      if (it.k === 'kp') {
        return `<a class="res" href="#/chapter/${it.c}?tab=notes&anchor=${it.a}">
          <div class="r-top"><span class="tag kp">知识点 · ${esc(it.kd || '')}</span>
            <span>第 ${it.c} 章 ${esc(it.ct || '')}</span><span>第 ${it.v} 集 ${fmtDur(it.t)}</span>
            <span class="r-go">打开笔记 →</span></div>
          <div class="r-text">${hl(it.x, query)}</div></a>`;
      }
      return `<a class="res" href="#/chapter/${it.c}?tab=${it.k === 'ex' ? 'ex' : it.k === 'exam' ? 'exam' : 'notes'}${it.a ? '&anchor=' + it.a : ''}">
        <div class="r-top"><span class="tag note">${it.k === 'ex' ? '课后习题' : it.k === 'exam' ? '考研真题' : '复习笔记'}</span>
          <span>第 ${it.c} 章 ${esc(it.ct || '')}</span><span>p.${it.p}</span><span class="r-go">${it.k === 'ex' && it.no ? '题 ' + esc(it.no) : '打开 →'}</span></div>
        <div class="r-text">${hl(it.x, query)}</div></a>`;
    }).join('');
    renderMathIn(results);
  }

  $$('.filters .chip').forEach(b => b.addEventListener('click', () => {
    kind = b.dataset.k;
    $$('.filters .chip').forEach(x => x.classList.toggle('on', x === b));
    run();
  }));
  let tmr; input.addEventListener('input', () => { clearTimeout(tmr); tmr = setTimeout(run, 220); });
  input.addEventListener('keydown', e => { if (e.key === 'Enter') run(); });
  if (initial) run(); else results.innerHTML = '<div class="empty">输入关键词开始搜索</div>';
}

// ---------------- boot ----------------
function wireChrome() {
  $('#menuBtn').addEventListener('click', () => {
    setSidebar(!$('#sidebar').classList.contains('open'));
  });
  $('#scrim').addEventListener('click', () => setSidebar(false));

  const themeBtn = $('#btnTheme');
  if (themeBtn) themeBtn.addEventListener('click', () => {
    applyTheme(document.documentElement.dataset.theme === 'dark' ? 'light' : 'dark');
  });
  const fsUp = $('#btnFsUp'), fsDown = $('#btnFsDown');
  if (fsUp) fsUp.addEventListener('click', () => stepFs(1));
  if (fsDown) fsDown.addEventListener('click', () => stepFs(-1));

  const lb = $('#lightbox');
  if (lb) {
    lb.addEventListener('click', e => { if (e.target === lb || e.target.classList.contains('lb-close')) closeLightbox(); });
  }
  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (!$('#lightbox').hidden) { closeLightbox(); return; }
    if ($('#sidebar').classList.contains('open')) { setSidebar(false); return; }
    if (S.editMode) setEditMode(false);
  });

  // ---- 编辑原文：开关 / 撤销 / 恢复 / 复原 ----
  const editBtn = $('#btnEdit');
  if (editBtn) editBtn.addEventListener('click', () => setEditMode(!S.editMode));
  document.addEventListener('click', e => {
    const shot = e.target.closest && e.target.closest('.kp-shot');
    if (shot && shot.src) { e.preventDefault(); openLightbox(shot.src); }
  });
  const edUndo = $('#edUndo'), edRedo = $('#edRedo'), edExit = $('#edExit'), edReset = $('#edReset');
  if (edUndo) edUndo.addEventListener('click', () => editUndoStep());
  if (edRedo) edRedo.addEventListener('click', () => editRedoStep());
  if (edExit) edExit.addEventListener('click', () => setEditMode(false));
  let resetArm = 0;
  if (edReset) edReset.addEventListener('click', () => {
    if (!Object.keys(S.store.edits).length) { toast('还没有任何修改'); return; }
    if (!resetArm) {
      edReset.classList.add('armed');
      edReset.textContent = '再点一次确认';
      resetArm = setTimeout(() => {
        resetArm = 0; edReset.classList.remove('armed'); edReset.textContent = '复原全部';
      }, 3200);
      return;
    }
    clearTimeout(resetArm); resetArm = 0;
    edReset.classList.remove('armed'); edReset.textContent = '复原全部';
    editResetAll();
  });
  document.addEventListener('keydown', e => {
    if (!S.editMode) return;
    if (e.target.closest && e.target.closest('#noteEditor')) return; // 笔记框用浏览器自带撤销
    const k = (e.key || '').toLowerCase();
    if ((e.ctrlKey || e.metaKey) && k === 'z') {
      e.preventDefault();
      if (e.shiftKey) editRedoStep(); else editUndoStep();
    } else if ((e.ctrlKey || e.metaKey) && k === 'y') {
      e.preventDefault();
      editRedoStep();
    }
  }, true);

  // 滑动手势关侧栏
  let sx = 0, sy = 0;
  $('#sidebar').addEventListener('touchstart', e => {
    sx = e.touches[0].clientX; sy = e.touches[0].clientY;
  }, { passive: true });
  $('#sidebar').addEventListener('touchend', e => {
    const t = e.changedTouches[0];
    if (sx - t.clientX > 60 && Math.abs(sy - t.clientY) < 60) setSidebar(false);
  }, { passive: true });

  const go = v => { if (v.trim()) location.hash = `#/search?q=${encodeURIComponent(v.trim())}`; };
  $('#sideSearch').addEventListener('keydown', e => { if (e.key === 'Enter') go(e.target.value); });
  $('#topSearch').addEventListener('keydown', e => { if (e.key === 'Enter') go(e.target.value); });
}

(async function boot() {
  applyTheme(S.store.theme === 'dark' ? 'dark' : 'light');
  applyFs(S.store.fs || 1);
  wireChrome();
  try {
    [S.course, S.videos] = await Promise.all([getJSON('data/course.json'), getJSON('data/videos.json')]);
    S.subs = await getJSON('data/subs/index.json').catch(() => null);
    S.hasBook = await fetch('books/notes.pdf', { method: 'HEAD', cache: 'no-store' })
      .then(r => r.ok).catch(() => false);
    if (!S.hasBook) document.querySelectorAll('a[href^="books/notes.pdf"]').forEach(a => a.remove());
  } catch (e) {
    $('#view').innerHTML = '<div class="empty">数据加载失败：' + esc(e.message) + '<br><br>请通过 start.cmd 启动的本地服务访问（不要直接双击 index.html）。</div>';
    return;
  }
  renderNav();
  window.addEventListener('hashchange', route);
  route();
})();
