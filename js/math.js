// 公式美化：把 PDF 抽平成纯文本的数学片段用 KaTeX 重新渲染
// 设计要点：只处理「像公式」的文本节点；图片公式（figure/img）一律不碰；
// KaTeX 未加载或解析失败时原样保留文本。

const SKIP_SEL = [
  'figure', 'figcaption', 'img', 'pre', 'code', 'textarea', 'input', 'script', 'style',
  '.katex', '.kp-src', '.tr-line', '.tr-empty', '.v-meta', '.v-links', '.r-top', '.r-go',
  '.crumbs', '.chapter-nav', '.chapter-videos', '.side-foot', '.dur', '.idx', '.hint',
  '.note-card', '.notice', '.ex-no', '.vitem', '.ch-card', '.progress-mini', '.fs-ctl',
].join(',');

const GREEK = {
  'α': '\\alpha', 'β': '\\beta', 'γ': '\\gamma', 'δ': '\\delta', 'ε': '\\varepsilon',
  'ζ': '\\zeta', 'η': '\\eta', 'θ': '\\theta', 'ι': '\\iota', 'κ': '\\kappa',
  'λ': '\\lambda', 'μ': '\\mu', 'µ': '\\mu', 'ν': '\\nu', 'ξ': '\\xi', 'π': '\\pi',
  'ρ': '\\rho', 'σ': '\\sigma', 'τ': '\\tau', 'υ': '\\upsilon', 'φ': '\\varphi',
  'ϕ': '\\phi', 'χ': '\\chi', 'ψ': '\\psi', 'ω': '\\omega',
  'Γ': '\\Gamma', 'Δ': '\\Delta', 'Θ': '\\Theta', 'Λ': '\\Lambda', 'Ξ': '\\Xi',
  'Π': '\\Pi', 'Σ': '\\Sigma', 'Υ': '\\Upsilon', 'Φ': '\\Phi', 'Ψ': '\\Psi', 'Ω': '\\Omega',
};

const SYM = {
  '×': '\\times ', '÷': '\\div ', '·': '\\cdot ', '⋅': '\\cdot ', '±': '\\pm ',
  '≤': '\\le ', '≥': '\\ge ', '≠': '\\ne ', '≈': '\\approx ', '≡': '\\equiv ',
  '∞': '\\infty ', '∠': '\\angle ', '∂': '\\partial ', '∑': '\\sum ', '∏': '\\prod ',
  '∫': '\\int ', '→': '\\to ', '←': '\\leftarrow ', '⇒': '\\Rightarrow ', '⇔': '\\Leftrightarrow ',
  '√': '\\sqrt ', '′': "'", '″': "''", '∝': '\\propto ', '⊥': '\\perp ', '∥': '\\parallel ', '°': '^{\\circ}',
  '−': '-', '–': '-', '—': '-', '＊': '*', '／': '/', '＝': '=', '＋': '+', '－': '-',
  '＊': '*', '％': '\\%', '%': '\\%', '∶': ':', '（': '(', '）': ')', 'İ': '\\dot{I}',
};

// 这些多字母词按正体函数名渲染（不要拆成上下标）
const FUNCS = ['arcsin', 'arccos', 'arctan', 'cosh', 'sinh', 'tanh', 'cos', 'sin', 'tan',
  'cot', 'sec', 'csc', 'ln', 'lg', 'log', 'exp', 'max', 'min', 'rms', 'avg', 'lim',
  'Re', 'Im', 'arg', 'sgn', 'var', 'eff', 'rms'];
// 其中这些是 KaTeX 内置命令（\sin 等），其余的（var / eff / rms / avg）用 \operatorname{}，
// 否则会变成未定义命令 \var 直接渲染失败
const LATEX_FUNCS = new Set(['arcsin', 'arccos', 'arctan', 'cosh', 'sinh', 'tanh', 'cos',
  'sin', 'tan', 'cot', 'sec', 'csc', 'ln', 'lg', 'log', 'exp', 'max', 'min', 'lim']);
// 这些缩写整体正体，不当作「字母 + 上标/下标」
const ACRONYMS = ['KCL', 'KVL', 'RLC', 'RC', 'RL', 'LC', 'CL', 'DC', 'AC', 'RMS', 'EMF',
  'VCVS', 'VCCS', 'CCVS', 'CCCS', 'SSS', 'CSC', 'IEC', 'PDF', 'SR'];
// 常见的「两字母大写下标」：端口/电极名（RAB、UOC、ISC、UCE、UDS…）
const UP_TAILS = new Set(['AB', 'OC', 'SC', 'CC', 'BE', 'CE', 'CB', 'DS', 'GS', 'SS', 'EF',
  'EQ', 'RE', 'IM', 'SR', 'TH', 'NO', 'NE', 'PK', 'PP', 'AV', 'UV', 'AN', 'BN', 'CN',
  'NN', 'BC', 'CA', 'LN', 'CR', 'BR', 'DF', 'DN', 'EN', 'EP', 'ES', 'MN', 'MP', 'PN',
  'SN', 'TN', 'TP', 'ZP', 'ZN', 'ZM', 'ZL', 'SL', 'CL', 'RL', 'PL', 'QL']);
// 基字母后「单个大写」可以当下标的集合（US → U_S、IL → I_L、UL → U_L）
const ONE_TAILS = 'SCRLNAB';
// 单位后缀：数字后紧跟这些时按正体单位处理
const UNITS = ['mA', 'kV', 'mV', 'MΩ', 'kΩ', 'MVar', 'kVar', 'var', 'VA', 'Hz', 'kHz',
  'MHz', 'rad', 'ms', 'us', 'μs', 'Ω', 'V', 'A', 'W', 'F', 'H', 'S', 's', 'T', 'N', 'J'];

const isDigit = c => c >= '0' && c <= '9';
const isLetter = c => (c >= 'A' && c <= 'Z') || (c >= 'a' && c <= 'z');
const isGreek = c => !!GREEK[c];
const isCJK = c => /[\u4e00-\u9fff\u3400-\u4dbf]/.test(c);
const isSpace = c => c === ' ' || c === '\u00a0' || c === '\u2009';

// 组成「公式片段」的字符（不含中文，中文在扫描时按条件并入）
const SOFT = new Set(['+', '-', '*', '/', '=', '(', ')', '[', ']', '{', '}', '<', '>', ',',
  '.', ':', ';', '^', '_', '|', '~', '\\', '"', "'", '%', '!', '?', '&', '$', '#', '@']);

const STRONG = /[=＝≤≥≠±∑∏∫√∠∞∂→←⇒⇔×÷·⋅<>∝]/;

// 组合用点（U̇ = U + 组合点）、撇号（N′）都要能进片段
const COMBO = new Set(['\u0307', '\u0308', '\u0304', '\u0305', '\u2032', '\u2033']);

function allowed(c) {
  return isLetter(c) || isDigit(c) || isGreek(c) || isSpace(c) || SOFT.has(c) || !!SYM[c] || COMBO.has(c);
}

// 纯图号 / 纯数字（如 1-1-1、3、2.5）不算公式
function trivial(run) {
  const t = run.replace(/\s+/g, '');
  if (!t) return true;
  if (/^[0-9]+([.\-–][0-9]+)+$/.test(t)) return true;   // 1-1-1 / 2.5
  if (/^[0-9.]+$/.test(t)) return true;                 // 3 / 2.5
  if (/^[0-9]+[.、]?$/.test(t)) return true;
  return false;
}

function mathish(run) {
  const t = run.replace(/\s+/g, '');
  if (t.length < 2) return false;
  if (trivial(t)) return false;
  if (!/[A-Za-z0-9α-ωΑ-Ωθφωλμπβ]/.test(t)) return false;
  if (STRONG.test(t)) return true;
  if (/根号|√/.test(run)) return true;
  if (/[A-Za-zα-ωΑ-Ω]\s*[0-9]/.test(t)) return true;       // R1 / u2 / ω2
  if (/[0-9]\s*(mA|kV|mV|MΩ|kΩ|var|VA|Hz|rad|Ω|V|A|W|F|H|s)\s*$/.test(t)) return false; // 单位结尾但无运算，交给普通文本
  if (/^[A-Z][A-Z]{1,2}$/.test(t) && !ACRONYMS.includes(t)) return true;                    // UAN / UBC 这类「变量+下标」
  if (/^[A-Za-z]\s*[A-Za-z]\s*[A-Za-z]+$/.test(t) && !ACRONYMS.includes(t)) return false;   // 普通英文单词
  if (/^[A-Za-z]{1,2}$/.test(t) && /[uiIRUCPLWZYG]{1,2}/.test(t)) return true;              // 单字母变量
  if (/[′']/.test(t)) return true;
  if (/[A-Za-z)]\s*[A-Za-z]\s*\/\s*[A-Za-z(]/.test(t)) return true;                          // di/dt、u/i
  return false;
}

// 把一个「公式片段」翻译成 TeX
// 把 d?/d?（含 dX_C/dt、dX(t)/dt）解析成 rac{}{}，返回 {tex, len} 或 null
function tryDeriv(s, i) {
  const n = s.length;
  let j = i;
  if (s[j] !== 'd') return null;
  j++;
  while (j < n && isSpace(s[j])) j++;
  const A_GREEK = /[Α-ω]/;
  const readSym = (k) => {
    if (k >= n || !(isLetter(s[k]) || A_GREEK.test(s[k]))) return null;
    let sym = s[k];
    k++;
    let sub = '';
    if (/[A-Z]/.test(s[k] || '') && !/[A-Z]/.test(s[k + 1] || '')) { sub = s[k]; k++; }
    else if (/[a-z]/.test(s[k] || '') && /[(（_]/.test(s[k + 1] || '')) { sub = s[k]; k++; }
    if (s[k] === '_') {
      if (s[k + 1] === '{') {
        const m = s.indexOf('}', k);
        if (m < 0) return null;
        sub = s.slice(k + 2, m);
        k = m + 1;
      } else {
        let m = k + 1;
        while (m < n && (isLetter(s[m]) || isDigit(s[m]))) m++;
        sub = s.slice(k + 1, m);
        k = m;
      }
    }
    let arg = '';
    if (s[k] === '(') {
      let d = 0, m = k;
      for (; m < n; m++) {
        if (s[m] === '(') d++;
        else if (s[m] === ')') { d--; if (d === 0) break; }
      }
      if (m < n) { arg = s.slice(k, m + 1).replace(/[()]/g, ''); k = m + 1; }
    }
    return { sym, sub, arg, next: k };
  };
  const a = readSym(j);
  if (!a) return null;
  j = a.next;
  while (j < n && isSpace(s[j])) j++;
  if (s[j] !== '/') return null;
  j++;
  while (j < n && isSpace(s[j])) j++;
  if (s[j] !== 'd') return null;
  j++;
  while (j < n && isSpace(s[j])) j++;
  const b = readSym(j);
  if (!b) return null;
  const greek = (ch) => (GREEK[ch] ? GREEK[ch] + ' ' : ch);
  const tex = '\\dfrac{d' + (a.sym === '1' ? '' : greek(a.sym)) + (a.sub ? '_{' + greek(a.sub) + '}' : '')
    + (a.arg ? '(' + toTeX(a.arg) + ')' : '') + '}{d' + greek(b.sym) + (b.sub ? '_{' + greek(b.sub) + '}' : '') + (b.arg ? '(' + toTeX(b.arg) + ')' : '') + '}';
  return { tex, len: b.next - i };
}

function toTeX(run) {
  const s = run.replace(/\s+/g, ' ')
    .replace(/（/g, '(').replace(/）/g, ')')
    .trim()
    // 电子电路的指数写法（题解里被抽平成 10－3、e－25t）按上标还原
    .replace(/(\b10|\))\s*[－\-−]\s*(\d)/g, '$1^{-$2}')
    .replace(/e\s*[－\-−]\s*(\d+[A-Za-z]?)/g, 'e^{-$1}');
  let out = '';
  let i = 0;
  const n = s.length;
  let sub = '';   // 待落的上下标
  const flush = () => { if (sub) { out += sub; sub = ''; } };

  while (i < n) {
    const c = s[i];
    if (isSpace(c)) { i++; continue; }
    if (c === 'd') { const dv = tryDeriv(s, i); if (dv) { out += dv.tex; i += dv.len; continue; } }
    if (c >= '̀' && c <= 'ͯ') { i++; continue; }   // 其余组合符（重音）忽略

    // 中文：紧跟在变量后面的 1~2 个字做正体下标（P吸 → P_{吸}）
    if (isCJK(c) && !s.startsWith('根号', i)) {
      let j = i; while (j < n && isCJK(s[j])) j++;
      out += `\\text{${s.slice(i, j)}}`;
      i = j; continue;
    }

    // 上标/下标显式标记；连续下划线是填空题的空位
    if (c === '^' || c === '_' || c === '′' || c === "'") {
      if (c === "'" || c === '′') { out += "'"; i++; continue; }
      if (c === '_') {
        let k = i; while (k < n && (s[k] === '_' || isSpace(s[k]))) k++;
        const unders = s.slice(i, k).replace(/\s/g, '');
        if (unders.length >= 2) { out += '\\underline{\\hspace{1.2em}}'; i = k; continue; }
      }
      let j = i + 1;
      while (j < n && isSpace(s[j])) j++;
      if (j < n && s[j] === '{') { out += c + '{'; i = j + 1; continue; }
      if (j < n) { out += c + '{' + s[j] + '}'; i = j + 1; continue; }
      i++; continue;
    }

    // 希腊字母
    if (isGreek(c)) {
      out += GREEK[c] + ' '; i++;
      // 希腊字母后跟数字 → 下标
      let j = i, d = '';
      while (j < n && isDigit(s[j]) && d.length < 2) { d += s[j]; j++; }
      if (d) { out += `_{${d}}`; i = j; }
      continue;
    }

    // 字母
    if (isLetter(c)) {
      // 导数 d?/d? → \frac{}{}
      const dm = /^d\s*([A-Za-z])\s*\/\s*d\s*([A-Za-z])/.exec(s.slice(i));
      if (dm) { out += `\\frac{d${dm[1]}}{d${dm[2]}}`; i += dm[0].length; continue; }
      // 多字母函数名
      let fn = FUNCS.find(w => s.startsWith(w, i));
      // 缩写整体正体
      let ac = ACRONYMS.find(w => s.startsWith(w, i));
      if (ac && (i + ac.length === n || !isLetter(s[i + ac.length]))) {
        out += `\\mathrm{${ac}}`; i += ac.length; continue;
      }
      if (fn && !(isLetter(s[i + fn.length] || ''))) {
        out += LATEX_FUNCS.has(fn) ? `\\${fn} ` : `\\operatorname{${fn}}`;
        i += fn.length; continue;
      }
      const base = c;
      i++;
      // 组合点：U̇ → \dot{U}
      let head = base;
      if (s[i] === '̇') { head = `\\dot{${base}}`; i++; }
      out += head;
      // 激进猜测：字母后面紧跟的数字 / 大写字母串 / 小写补语 → 下标
      let j = i, tail = '';
      while (j < n && isDigit(s[j]) && tail.length < 2) { tail += s[j]; j++; }
      // j31.4 这种其实是小数，不要当上下标
      if (tail && s[j] === '.' && isDigit(s[j + 1] || '')) { tail = ''; j = i; }
      if (!tail) {
        let k = i, up = '';
        while (k < n && up.length < 4) {
          const ck = s[k];
          if (/[A-Z]/.test(ck)) { up += ck; k++; continue; }
          // 撇号只在「N′N」这种复合下标里保留；uS′ / i′ 的撇号属于变量本身
          if ((ck === '′' || ck === "'") && /[A-Z]/.test(s[k + 1] || '')) { up += ck; k++; continue; }
          break;
        }
        const upPlain = up.replace(/[′']/g, "'");
        const hasPrime = /[′']/.test(up);
        // 只要带撇号（N′N / U′N）就整串当下标
        let ok = false;
        if (hasPrime && upPlain.length >= 2) ok = true;
        else if (up.length === 2) ok = UP_TAILS.has(up);
        else if (up.length === 1) ok = 'uiev'.includes(base) || ONE_TAILS.includes(up);
        if (ok && !ACRONYMS.includes(base + up)) { tail = upPlain; j = k; }
        else if (!ok && up.length >= 2) {
          // 长串退回：只取一个字母当下标（USI = U_S·I）
          const one = up[0];
          if ('uiev'.includes(base) || ONE_TAILS.includes(one)) { tail = one; j = i + 1; }
        }
      }
      if (!tail) {
        let k = i, low = '';
        while (k < n && isLetter(s[k]) && /[a-z]/.test(s[k]) && low.length < 3) { low += s[k]; k++; }
        if (low.length >= 2 && /[A-Z]/.test(base) && s[k] !== '/' && !/[A-Z]/.test(s[k] || '')) {
          tail = low; j = k;
          if (FUNCS.includes(low) || ACRONYMS.includes(low.toUpperCase())) { tail = ''; j = i; }
        }
      }
      if (tail) {
        out += `_{${tail}}`; i = j;
      }
      continue;
    }

    // 数字（含小数）后接单位
    if (isDigit(c) || c === '.') {
      let j = i, num = '';
      while (j < n && (isDigit(s[j]) || s[j] === '.')) { num += s[j]; j++; }
      if (num) {
        out += num;
        const u = UNITS.find(w => s.startsWith(w, j) && !isLetter(s[j + w.length] || ''));
        if (u && /[0-9]$/.test(num)) { out += `\\,\\mathrm{${u}}`; j += u.length; }
        i = j; continue;
      }
    }

    // 根号：根号3ULILcosφ → \\sqrt{3}U_{L}I_{L}\\cos φ；√（a2＋b2）→ \\sqrt{a_{2}+b_{2}}
    if (c === '√' || s.startsWith('根号', i)) {
      i += (c === '√' ? 1 : 2);
      let k = i;
      while (k < n && isSpace(s[k])) k++;
      if (s[k] === '(' || s[k] === '（') {
        let depth = 0, m2 = k;
        for (; m2 < n; m2++) {
          if (s[m2] === '(' || s[m2] === '（') depth++;
          else if (s[m2] === ')' || s[m2] === '）') { depth--; if (depth === 0) break; }
        }
        if (m2 < n) { out += '\\sqrt{' + toTeX(s.slice(k + 1, m2)) + '}'; i = m2 + 1; continue; }
      }
      // 数字（可带单位）或单个字母 一并放进根号
      let m3 = k, num = '';
      while (m3 < n && (isDigit(s[m3]) || (s[m3] === '.' && isDigit(s[m3 + 1] || '')))) { num += s[m3]; m3++; }
      if (num) {
        const u = UNITS.find(w => s.startsWith(w, m3) && !isLetter(s[m3 + w.length] || ''));
        if (u) { out += '\\sqrt{' + num + '\\,\\mathrm{' + u + '}}'; i = m3 + u.length; continue; }
        out += '\\sqrt{' + num + '}'; i = m3; continue;
      }
      if (k < n && isLetter(s[k])) { out += '\\sqrt{' + s[k] + '}'; i = k + 1; continue; }
      out += '\\sqrt{}'; continue;
    }
    // 其余符号
    if (SYM[c] !== undefined) { out += SYM[c]; i++; continue; }
    if (c.charCodeAt(0) > 127) { out += '\\text{' + c + '}'; i++; continue; }
    out += c; i++;
  }
  flush();
  return out.replace(/\s+/g, ' ').trim();
}

function balanced(tex) {
  let d = 0;
  for (const c of tex) {
    if (c === '{') d++;
    else if (c === '}') { d--; if (d < 0) return false; }
  }
  return d === 0;
}

// 扫描一段文本，返回 [{start, end, tex}] 形式的可渲染片段
export function findMath(text) {
  const segs = [];
  const n = text.length;
  let i = 0;
  while (i < n) {
    const c = text[i];
    const gen = text.startsWith('根号', i);   // 「根号」当作根号符号
    const canStart = isLetter(c) || isDigit(c) || isGreek(c) || c === '(' || c === '（' || c === '√' || gen;
    if (!canStart) { i++; continue; }

    // 从 i 起尽可能向后吃字符（中文不并入，但「根号」例外）
    let j = i, strong = false;
    while (j < n) {
      if (text.startsWith('根号', j)) { strong = true; j += 2; continue; }
      const ch = text[j];
      if (isCJK(ch)) break;
      if (!allowed(ch)) break;
      if (STRONG.test(ch)) strong = true;
      j++;
    }

    const run = text.slice(i, j);
    const trimmed = run.replace(/[\s.]+$/, '');
    const end = i + trimmed.length;
    if (trimmed && mathish(trimmed)) {
      const tex = toTeX(trimmed);
      if (tex && balanced(tex) && tex.length >= 1 && !/^[0-9.\s\\,a-z]+$/.test(tex)) {
        segs.push({ start: i, end, tex });
      }
    }
    i = end > i ? end : i + 1;
  }
  return segs;
}

let warned = false;
function renderNode(node) {
  const text = node.nodeValue;
  if (!text || text.length < 2) return;
  if (!/[A-Za-z0-9α-ωΑ-Ωθφωλμπβ＝=≤≥≠±∑∫√∠∞×÷·]/.test(text)) return;
  const segs = findMath(text);
  if (!segs.length) return;
  const katex = window.katex;
  const frag = document.createDocumentFragment();
  let last = 0;
  for (const s of segs) {
    let html;
    try {
      html = katex.renderToString(s.tex, {
        throwOnError: false, displayMode: false, output: 'html',
        strict: 'ignore', trust: false, maxSize: 12,
      });
    } catch (e) { continue; }
    if (!html) continue;
    if (s.start > last) frag.appendChild(document.createTextNode(text.slice(last, s.start)));
    const span = document.createElement('span');
    span.className = 'math-inline';
    span.innerHTML = html;
    frag.appendChild(span);
    last = s.end;
  }
  if (!last) return;
  if (last < text.length) frag.appendChild(document.createTextNode(text.slice(last)));
  node.parentNode.replaceChild(frag, node);
}

// 对外入口：对 root 内的文本节点做美化（幂等：第二次运行没有未处理的文本）
export function renderMathIn(root) {
  if (!root || !window.katex) {
    if (!warned) { warned = true; console.info('[math] KaTeX 未加载，公式按纯文本显示'); }
    return 0;
  }
  const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
    acceptNode(node) {
      if (!node.nodeValue || !node.nodeValue.trim()) return NodeFilter.FILTER_REJECT;
      const p = node.parentElement;
      if (!p) return NodeFilter.FILTER_REJECT;
      if (p.closest(SKIP_SEL)) return NodeFilter.FILTER_REJECT;
      return NodeFilter.FILTER_ACCEPT;
    },
  });
  const nodes = [];
  let cur;
  while ((cur = walker.nextNode())) nodes.push(cur);
  let count = 0;
  for (const node of nodes) { try { renderNode(node); count++; } catch (e) { /* 单个节点失败不影响整体 */ } }
  return count;
}
