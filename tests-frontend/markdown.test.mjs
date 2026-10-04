// Markdown 解析器行为与安全用例（纯数据层,无 DOM,无依赖）
// 运行: node --test tests-frontend/
import { test } from 'node:test';
import assert from 'node:assert/strict';
import { createRequire } from 'node:module';
const require = createRequire(import.meta.url);
const MD = require('../static/markdown.js');

function types(blocks) { return blocks.map((b) => b.type); }
function inlineText(tokens) {
  return tokens.map((t) => t.text || (t.children ? inlineText(t.children) : '')).join('');
}

test('标题层级', () => {
  const b = MD.parseMarkdown('# 一\n## 二\n### 三\n普通文本');
  assert.deepEqual(types(b), ['heading', 'heading', 'heading', 'paragraph']);
  assert.deepEqual([b[0].level, b[1].level, b[2].level], [1, 2, 3]);
  assert.equal(b[0].text, '一');
});

test('段落与软换行', () => {
  const b = MD.parseMarkdown('第一行\n第二行\n\n新段落');
  assert.equal(b.length, 2);
  assert.equal(b[0].text, '第一行\n第二行');
});

test('无序/有序列表与嵌套', () => {
  const b = MD.parseMarkdown('- a\n- b\n  - b1\n  - b2\n- c\n\n1. x\n2. y');
  assert.deepEqual(types(b), ['list', 'list']);
  assert.equal(b[0].ordered, false);
  assert.equal(b[0].items.length, 3);
  assert.equal(b[0].items[1].children[0].items.length, 2);
  assert.equal(b[1].ordered, true);
});

test('引用与递归内容', () => {
  const b = MD.parseMarkdown('> 引用\n> - 列表项\n\n正文');
  assert.equal(b[0].type, 'quote');
  assert.deepEqual(types(b[0].blocks), ['paragraph', 'list']);
});

test('分隔线', () => {
  const b = MD.parseMarkdown('上\n\n---\n\n下');
  assert.deepEqual(types(b), ['paragraph', 'hr', 'paragraph']);
});

test('GFM 表格与对齐', () => {
  const b = MD.parseMarkdown('| 名称 | 数量 | 价格 |\n|:-----|-----:|-----:|\n| 苹果 | 3 | 5.5 |\n| 梨 | 2 | 4 |');
  assert.equal(b[0].type, 'table');
  assert.deepEqual(b[0].aligns, ['left', 'right', 'right']);
  assert.equal(b[0].rows.length, 2);
  assert.deepEqual(b[0].header, ['名称', '数量', '价格']);
});

test('围栏代码块与语言标签', () => {
  const b = MD.parseMarkdown('```js\nconst a = 1;\n```\n\n文本');
  assert.equal(b[0].type, 'code');
  assert.equal(b[0].lang, 'js');
  assert.equal(b[0].code, 'const a = 1;');
});

test('未闭合围栏按到 EOF 处理', () => {
  const b = MD.parseMarkdown('前文\n\n```\n没有闭合\n还在代码里');
  assert.equal(b[1].type, 'code');
  assert.equal(b[1].code, '没有闭合\n还在代码里');
});

test('行内:粗体/斜体/行内代码', () => {
  const t = MD.inlineTokens('**粗** 和 *斜* 和 `code` 结束');
  assert.equal(t[0].t, 'bold');
  assert.equal(t.find((x) => x.t === 'italic').children[0].text, '斜');
  assert.equal(t.find((x) => x.t === 'code').text, 'code');
});

test('行内:下划线不拆单词内 snake_case', () => {
  const t = MD.inlineTokens('lease_token 不应斜体');
  assert.equal(t.length, 1);
  assert.equal(t[0].t, 'text');
});

test('行内:未闭合标记按文本', () => {
  const t = MD.inlineTokens('**没闭合 和 `也没闭合');
  assert.ok(t.every((x) => x.t === 'text'));
});

test('链接:合法 http(s) 保留', () => {
  const t = MD.inlineTokens('[论坛](https://bbs.hamlet.ink) 和 https://example.com/a');
  const links = t.filter((x) => x.t === 'link');
  assert.equal(links.length, 2);
  assert.equal(links[0].href, 'https://bbs.hamlet.ink/');
});

test('安全:javascript:/data: 协议拒绝', () => {
  const t = MD.inlineTokens('[点我](javascript:alert(1)) [x](data:text/html,<script>)');
  assert.ok(t.every((x) => x.t !== 'link'), JSON.stringify(t));
});

test('安全:原始 HTML 只是文本', () => {
  const t = MD.inlineTokens('<script>alert(1)</script> <img src=x onerror=alert(1)>');
  assert.ok(t.every((x) => x.t === 'text'));
  assert.equal(inlineText(t), '<script>alert(1)</script> <img src=x onerror=alert(1)>');
});

test('安全:图片退化为链接且不产生 img 语义', () => {
  const t = MD.inlineTokens('![截图](https://example.com/a.png)');
  assert.equal(t.length, 1);
  assert.equal(t[0].t, 'link');
  assert.equal(t[0].image, true);
  assert.equal(t[0].text, '🖼 截图');
});

test('安全:图片危险协议按纯文本', () => {
  const t = MD.inlineTokens('![x](javascript:alert(1))');
  assert.ok(t.every((x) => x.t === 'text'));
});

test('URL 末尾标点修剪与成对括号保留', () => {
  assert.equal(MD.trimUrl('https://a.com/x).'), 'https://a.com/x');
  assert.equal(MD.trimUrl('https://a.com/f_(x)'), 'https://a.com/f_(x)');
});

test('中文长文与混合结构不断裂', () => {
  const src = '# 交付报告\n\n本次修改 **15 个文件**,涉及 `formatters.js`。\n\n| 文件 | 变化 |\n|---|---|\n| app.js | +78 |\n\n1. 先跑基线\n2. 再施工\n\n> 注意:测试为准。\n\n```\nnode --test\n```';
  const b = MD.parseMarkdown(src);
  assert.deepEqual(types(b), ['heading', 'paragraph', 'table', 'list', 'quote', 'code']);
});

test('畸形输入不抛异常', () => {
  for (const src of ['', '\n\n\n', '```', '|', '|---|', '- ', '>', '#', '**', '[]()', '![', '|\n|---\n']) {
    MD.parseMarkdown(src);
    MD.inlineTokens(src);
  }
});

// ---------- #23 审查回归:缩进起始列表 / 深嵌套 ----------
test('回归:根列表带 1/2/3 空格缩进均正常解析且循环必终止', () => {
  for (const src of [' - item', '  - item', '   - item', '  1. item', '  + item']) {
    const b = MD.parseMarkdown(src);
    assert.equal(b[0].type, 'list', JSON.stringify(src));
    assert.equal(b[0].items.length, 1);
  }
});

test('回归:制表符缩进列表', () => {
  const b = MD.parseMarkdown('\t- item\n\t- item2');
  assert.equal(b[0].type, 'list');
  assert.equal(b[0].items.length, 2);
});

test('回归:引用内带缩进列表', () => {
  const b = MD.parseMarkdown('>   - item\n>   - item2');
  assert.equal(b[0].type, 'quote');
  assert.equal(b[0].blocks[0].type, 'list');
  assert.equal(b[0].blocks[0].items.length, 2);
});

test('回归:混合有序/无序列表各自成块且均终止', () => {
  const b = MD.parseMarkdown('- a\n- b\n1. x\n2. y\n- c');
  assert.deepEqual(types(b), ['list', 'list', 'list']);
  assert.equal(b[0].ordered, false);
  assert.equal(b[1].ordered, true);
  assert.equal(b[2].ordered, false);
});

test('回归:7000 深引用不栈溢出,安全退化', () => {
  const b = MD.parseMarkdown('>'.repeat(7000) + ' x');
  assert.equal(b.length, 1);
  assert.equal(b[0].type, 'quote'); // 顶层仍是引用,超限内部退化为文本
});

test('回归:超深列表缩进不栈溢出且必终止', () => {
  const src = Array.from({ length: 2000 }, (_, i) => ' '.repeat(i) + '- x').join('\n');
  const b = MD.parseMarkdown(src);
  assert.ok(b.length >= 1);
});

test('回归:深度超限后内容仍以文本保留,不丢数据', () => {
  const deep = '>'.repeat(30) + ' 保留我';
  const b = MD.parseMarkdown(deep);
  const flat = JSON.stringify(b);
  assert.ok(flat.includes('保留我'));
});
