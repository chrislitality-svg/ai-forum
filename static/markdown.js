// AI Forum 安全 Markdown 阅读器（无依赖）。
// 安全模型：解析为纯数据 AST；DOM 渲染只经 textContent/setAttribute，
// 链接走 http/https 白名单；原始 HTML 一律按文本显示；图片退化为安全链接。
'use strict';
(function (root, factory) {
  const api = factory();
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  root.MD = api;
})(typeof self !== 'undefined' ? self : globalThis, function () {

  // ---------- 链接 ----------
  function safeUrl(raw) {
    try {
      const url = new URL(String(raw).trim());
      return url.protocol === 'https:' || url.protocol === 'http:' ? url.href : null;
    } catch (_) {
      return null;
    }
  }

  const URL_RE = /https?:\/\/[^\s<>"'`，。；！？、（）【】「」]+/g;
  const TRAILING = /[).,;:!?\]}>*_~]+$/;
  function trimUrl(raw) {
    let url = raw;
    while (TRAILING.test(url)) {
      const last = url[url.length - 1];
      if (last === ')' && (url.match(/\(/g) || []).length >= (url.match(/\)/g) || []).length) break;
      url = url.slice(0, -1);
    }
    return url;
  }

  // ---------- 行内解析（纯数据 token） ----------
  // token: {t:'text'|'code'|'bold'|'italic'|'link', text?, href?, image?, children?}
  function inlineTokens(text) {
    // 1) 行内代码
    const out = [];
    for (const part of String(text).split(/(`[^`\n]+`)/)) {
      if (part.length > 2 && part.startsWith('`') && part.endsWith('`')) {
        out.push({ t: 'code', text: part.slice(1, -1) });
      } else if (part) {
        out.push(...inlineNoCode(part));
      }
    }
    return out;
  }

  function inlineNoCode(text) {
    // 2) 图片 / 链接
    const tokens = [];
    const LINK_RE = /(!?)\[([^\]\n]*)\]\(([^)\n]+)\)/g;
    let last = 0;
    for (const m of text.matchAll(LINK_RE)) {
      if (m.index > last) tokens.push(...inlineEmphasis(text.slice(last, m.index)));
      const href = safeUrl(m[3]);
      const label = m[2];
      if (m[1] === '!') {
        // 图片退化为安全链接，不加载远程资源
        tokens.push(href
          ? { t: 'link', href, text: `🖼 ${label || href}`, image: true }
          : { t: 'text', text: m[0] });
      } else {
        tokens.push(href
          ? { t: 'link', href, children: inlineEmphasis(label) }
          : { t: 'text', text: m[0] });
      }
      last = m.index + m[0].length;
    }
    if (last < text.length) tokens.push(...inlineEmphasis(text.slice(last)));
    return tokens;
  }

  function inlineEmphasis(text) {
    // 3) 粗体 / 斜体；未闭合按文本；下划线要求非单词内部
    const tokens = [];
    const RE = /(\*\*[^*\n]+\*\*|__[^_\n]+__|\*[^*\n]+\*|(?<![\w])_[^_\n]+_(?![\w]))/g;
    let last = 0;
    for (const m of text.matchAll(RE)) {
      if (m.index > last) tokens.push(...inlineUrls(text.slice(last, m.index)));
      const raw = m[0];
      if (raw.startsWith('**') || raw.startsWith('__')) tokens.push({ t: 'bold', children: inlineUrls(raw.slice(2, -2)) });
      else tokens.push({ t: 'italic', children: inlineUrls(raw.slice(1, -1)) });
      last = m.index + raw.length;
    }
    if (last < text.length) tokens.push(...inlineUrls(text.slice(last)));
    return tokens;
  }

  function inlineUrls(text) {
    const tokens = [];
    let last = 0;
    for (const m of text.matchAll(URL_RE)) {
      const url = trimUrl(m[0]);
      if (!url) continue;
      if (m.index > last) tokens.push({ t: 'text', text: text.slice(last, m.index) });
      const href = safeUrl(url);
      tokens.push(href ? { t: 'link', href, text: url } : { t: 'text', text: url });
      last = m.index + url.length;
    }
    if (last < text.length) tokens.push({ t: 'text', text: text.slice(last) });
    return tokens;
  }

  // ---------- 块级解析（纯数据 AST） ----------
  const RE_FENCE = /^```([^\n`]*)$/;
  // 嵌套深度上限：防畸形输入栈溢出；超限安全退化为文本
  const MAX_DEPTH = 20;
  const RE_HR = /^\s{0,3}(?:-{3,}|\*{3,}|_{3,})\s*$/;
  const RE_HEADING = /^(#{1,6})\s+(.*)$/;
  const RE_QUOTE = /^>\s?(.*)$/;
  const RE_ULIST = /^(\s*)[-*+]\s+(.*)$/;
  const RE_OLIST = /^(\s*)(\d+)[.)]\s+(.*)$/;
  const RE_TABLE_SEP = /^\|?[\s:|-]+\|[\s:|-]*$/;

  function looksLikeTable(lines, i) {
    if (i + 1 >= lines.length) return false;
    if (!RE_TABLE_SEP.test(lines[i + 1]) || !/-{3,}/.test(lines[i + 1])) return false;
    return lines[i].includes('|');
  }

  function splitRow(line) {
    let s = line.trim();
    if (s.startsWith('|')) s = s.slice(1);
    if (s.endsWith('|')) s = s.slice(0, -1);
    return s.split('|').map((c) => c.trim());
  }

  function parseMarkdown(text, depth = 0) {
    const lines = String(text).replace(/\r\n?/g, '\n').split('\n');
    const blocks = [];
    let i = 0;
    while (i < lines.length) {
      const line = lines[i];

      // 围栏代码块（允许未闭合，按到 EOF 处理）
      const fence = line.match(RE_FENCE);
      if (fence) {
        const buf = [];
        i += 1;
        while (i < lines.length && !RE_FENCE.test(lines[i])) { buf.push(lines[i]); i += 1; }
        if (i < lines.length) i += 1; // 跳过闭合行
        blocks.push({ type: 'code', lang: fence[1].trim(), code: buf.join('\n') });
        continue;
      }

      if (line.trim() === '') { i += 1; continue; }

      if (RE_HR.test(line)) { blocks.push({ type: 'hr' }); i += 1; continue; }

      const heading = line.match(RE_HEADING);
      if (heading) { blocks.push({ type: 'heading', level: heading[1].length, text: heading[2].trim() }); i += 1; continue; }

      // 引用（连续行，递归解析内部；深度超限退化为文本，防栈溢出）
      if (RE_QUOTE.test(line)) {
        const inner = [];
        while (i < lines.length && RE_QUOTE.test(lines[i])) { inner.push(lines[i].match(RE_QUOTE)[1]); i += 1; }
        if (depth >= MAX_DEPTH) {
          blocks.push({ type: 'paragraph', text: inner.join('\n') });
        } else {
          blocks.push({ type: 'quote', blocks: parseMarkdown(inner.join('\n'), depth + 1) });
        }
        continue;
      }

      // GFM 表格
      if (looksLikeTable(lines, i)) {
        const header = splitRow(lines[i]);
        const aligns = splitRow(lines[i + 1]).map((c) => {
          const left = c.startsWith(':'), right = c.endsWith(':');
          return left && right ? 'center' : right ? 'right' : 'left';
        });
        const rows = [];
        i += 2;
        while (i < lines.length && lines[i].includes('|') && lines[i].trim() !== '') {
          rows.push(splitRow(lines[i]));
          i += 1;
        }
        blocks.push({ type: 'table', header, aligns, rows });
        continue;
      }

      // 列表（有序/无序，按实际起始缩进嵌套；无进展时按段落保底，保证循环必前进）
      if (RE_ULIST.test(line) || RE_OLIST.test(line)) {
        const m = line.match(RE_ULIST) || line.match(RE_OLIST);
        const { list, next } = parseList(lines, i, m[1].length, 0);
        if (next === i || !list.items.length) {
          blocks.push({ type: 'paragraph', text: line });
          i += 1;
        } else {
          blocks.push(list);
          i = next;
        }
        continue;
      }

      // 段落：吃到空行或下一个块级起点
      const buf = [line];
      i += 1;
      while (i < lines.length && lines[i].trim() !== '' &&
             !RE_FENCE.test(lines[i]) && !RE_HR.test(lines[i]) && !RE_HEADING.test(lines[i]) &&
             !RE_QUOTE.test(lines[i]) && !RE_ULIST.test(lines[i]) && !RE_OLIST.test(lines[i]) &&
             !looksLikeTable(lines, i)) {
        buf.push(lines[i]);
        i += 1;
      }
      blocks.push({ type: 'paragraph', text: buf.join('\n') });
    }
    return blocks;
  }

  function parseList(lines, i, indent, depth) {
    const ordered = RE_OLIST.test(lines[i]) && (lines[i].match(RE_OLIST)[1].length === indent);
    const items = [];
    while (i < lines.length) {
      const line = lines[i];
      const ul = line.match(RE_ULIST);
      const ol = line.match(RE_OLIST);
      const m = ordered ? ol : ul;
      if (!m || m[1].length !== indent) break;
      if ((ordered && !ol) || (!ordered && !ul)) break;
      const item = { text: m[ordered ? 3 : 2], children: [] };
      i += 1;
      // 嵌套：下一行缩进更深且仍是列表；深度超限不再嵌套
      if (i < lines.length && depth < MAX_DEPTH) {
        const nu = lines[i].match(RE_ULIST);
        const no = lines[i].match(RE_OLIST);
        const nm = nu || no;
        if (nm && nm[1].length > indent) {
          const sub = parseList(lines, i, nm[1].length, depth + 1);
          item.children.push(sub.list);
          i = sub.next;
        }
      }
      items.push(item);
    }
    return { list: { type: 'list', ordered, items }, next: i };
  }

  // ---------- DOM 渲染（浏览器端；所有文本经 textContent） ----------
  function renderInline(tokens, el, into) {
    for (const tk of tokens) {
      if (tk.t === 'text') into.append(document.createTextNode(tk.text));
      else if (tk.t === 'code') into.append(el('code', null, tk.text));
      else if (tk.t === 'bold') into.append(el('strong', null, frag(el, tk.children)));
      else if (tk.t === 'italic') into.append(el('em', null, frag(el, tk.children)));
      else if (tk.t === 'link') {
        const a = el('a', { href: tk.href, target: '_blank', rel: 'noopener noreferrer nofollow', referrerpolicy: 'no-referrer' });
        if (tk.children) a.append(frag(el, tk.children));
        else a.textContent = tk.text;
        into.append(a);
      }
    }
    return into;
  }
  function frag(el, children) {
    const f = document.createDocumentFragment();
    renderInline(children, el, f);
    return f;
  }

  function renderBlocks(blocks, el, into) {
    for (const b of blocks) {
      if (b.type === 'code') {
        into.append(el('div', { class: 'codeblock' },
          b.lang ? el('span', { class: 'lang' }, b.lang) : null,
          el('pre', null, el('code', null, b.code))));
      } else if (b.type === 'hr') {
        into.append(el('hr'));
      } else if (b.type === 'heading') {
        const h = el('h' + Math.min(6, b.level + 1), { class: 'md-h md-h' + b.level });
        renderInline(inlineTokens(b.text), el, h);
        into.append(h);
      } else if (b.type === 'quote') {
        const q = el('blockquote', { class: 'md-quote' });
        renderBlocks(b.blocks, el, q);
        into.append(q);
      } else if (b.type === 'table') {
        const table = el('table', { class: 'md-table' });
        const thead = el('thead');
        const htr = el('tr');
        b.header.forEach((cell, idx) => {
          const th = el('th', b.aligns[idx] && b.aligns[idx] !== 'left' ? { class: 'md-a' + b.aligns[idx][0] } : null);
          renderInline(inlineTokens(cell), el, th);
          htr.append(th);
        });
        thead.append(htr);
        const tbody = el('tbody');
        for (const row of b.rows) {
          const tr = el('tr');
          row.forEach((cell, idx) => {
            const td = el('td', b.aligns[idx] && b.aligns[idx] !== 'left' ? { class: 'md-a' + b.aligns[idx][0] } : null);
            renderInline(inlineTokens(cell), el, td);
            tr.append(td);
          });
          tbody.append(tr);
        }
        table.append(thead, tbody);
        into.append(el('div', { class: 'md-table-scroll' }, table));
      } else if (b.type === 'list') {
        into.append(renderList(b, el));
      } else if (b.type === 'paragraph') {
        const p = el('p');
        b.text.split('\n').forEach((seg, idx) => {
          if (idx > 0) p.append(el('br'));
          renderInline(inlineTokens(seg), el, p);
        });
        into.append(p);
      }
    }
    return into;
  }

  function renderList(list, el) {
    const node = el(list.ordered ? 'ol' : 'ul', { class: 'md-list' });
    for (const item of list.items) {
      const li = el('li');
      renderInline(inlineTokens(item.text), el, li);
      for (const sub of item.children) li.append(renderList(sub, el));
      node.append(li);
    }
    return node;
  }

  // ---------- 阅读 / 原文 切换 ----------
  function richView(text, el) {
    const rendered = el('div', { class: 'rich md' });
    try {
      renderBlocks(parseMarkdown(text), el, rendered);
    } catch (_) {
      // 解析或渲染异常绝不能影响整帖打开：退化为完整文本
      rendered.replaceChildren(el('p', null, text));
    }
    const raw = el('pre', { class: 'md-raw', hidden: true }, text);
    const btnRender = el('button', { type: 'button', class: 'md-toggle active', 'aria-pressed': 'true' }, '阅读');
    const btnRaw = el('button', { type: 'button', class: 'md-toggle', 'aria-pressed': 'false' }, '原文');
    btnRender.addEventListener('click', () => {
      rendered.hidden = false; raw.hidden = true;
      btnRender.classList.add('active'); btnRender.setAttribute('aria-pressed', 'true');
      btnRaw.classList.remove('active'); btnRaw.setAttribute('aria-pressed', 'false');
    });
    btnRaw.addEventListener('click', () => {
      rendered.hidden = true; raw.hidden = false;
      btnRaw.classList.add('active'); btnRaw.setAttribute('aria-pressed', 'true');
      btnRender.classList.remove('active'); btnRender.setAttribute('aria-pressed', 'false');
    });
    return el('div', { class: 'mdview' },
      el('div', { class: 'md-toolbar', role: 'group', 'aria-label': '显示模式' }, btnRender, btnRaw),
      rendered, raw);
  }

  return { safeUrl, trimUrl, inlineTokens, parseMarkdown, renderBlocks, renderInline, richView };
});
