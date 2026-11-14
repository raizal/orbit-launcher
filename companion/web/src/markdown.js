// Renders a diary page the way Obsidian shows it: frontmatter, `![[audio]]`
// embeds as players, `> [!type]- title` callouts as collapsible blocks.
import { marked } from 'marked'
import DOMPurify from 'dompurify'

const AUDIO = /\.(m4a|aac|mp3|ogg|wav)$/i

const escape = (s) => s.replace(/[&<>"]/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' })[c])

/** `---\nkey: value\n---` at the top → [{ key, value }], rest of the text. */
function splitFrontmatter(text) {
  const m = text.match(/^---\n([\s\S]*?)\n---\n?/)
  if (!m) return { meta: [], body: text }
  const meta = m[1].split('\n').map((line) => {
    const i = line.indexOf(':')
    return i < 0 ? null : { key: line.slice(0, i).trim(), value: line.slice(i + 1).trim() }
  }).filter(Boolean)
  return { meta, body: text.slice(m[0].length) }
}

/** Obsidian wikilinks: `![[x.m4a]]` → audio player, `![[x]]` / `[[x|alias]]` → plain text. */
function wikilinks(md) {
  return md
    .replace(/!\[\[([^\]|]+?)(?:\|[^\]]*)?\]\]/g, (_, target) =>
      AUDIO.test(target)
        ? `<audio controls preload="none" src="/ui/api/vault/${target.split('/').map(encodeURIComponent).join('/')}"></audio>`
        : `<span class="wikilink">${escape(target)}</span>`)
    .replace(/\[\[([^\]|]+?)(?:\|([^\]]*))?\]\]/g, (_, target, alias) =>
      `<span class="wikilink">${escape(alias || target)}</span>`)
}

/** `> [!quote]- Title` + following `>` lines → <details> (collapsed when `-`). */
function callouts(md) {
  const lines = md.split('\n')
  const out = []
  for (let i = 0; i < lines.length; i++) {
    const head = lines[i].match(/^>\s*\[!(\w+)\]([+-]?)\s*(.*)$/)
    if (!head) {
      out.push(lines[i])
      continue
    }
    const inner = []
    while (i + 1 < lines.length && lines[i + 1].startsWith('>')) inner.push(lines[++i].replace(/^>\s?/, ''))
    const [, type, fold, title] = head
    const content = marked.parse(wikilinks(inner.join('\n')))
    out.push(
      `<details class="callout ${escape(type.toLowerCase())}"${fold === '-' ? '' : ' open'}>` +
        `<summary>${escape(title || type)}</summary>${content}</details>`,
      '',
    )
  }
  return out.join('\n')
}

export function renderDiary(text) {
  const { meta, body } = splitFrontmatter(text)
  const md = callouts(wikilinks(body.replace(/<!--[\s\S]*?-->/g, '')))
  const html = marked.parse(md)
  return {
    meta,
    html: DOMPurify.sanitize(html, { ADD_TAGS: ['audio'], ADD_ATTR: ['controls', 'preload', 'open'] }),
  }
}
