<script>
  // Memory (docs/memory.md): search the episodic index; view, edit, retire, delete facts; forget a word.
  // Loopback-only like the rest of /ui/api. SQLite is the source of truth; Obsidian's Memory.md is generated.
  import { onMount } from 'svelte'
  import { api, fmtDay, fmtTime } from './api.js'

  let { onclose } = $props()

  let stats = $state(null)
  let error = $state('')
  let q = $state('')
  let from = $state('')
  let to = $state('')
  let hits = $state(null)
  let searching = $state(false)

  async function refresh() {
    try {
      stats = await api.memoryStatus()
    } catch (e) {
      error = e.message
    }
  }

  async function search(e) {
    e?.preventDefault()
    searching = true
    error = ''
    try {
      hits = (await api.memorySearch(q, from, to)).hits
    } catch (err) {
      error = err.message
    } finally {
      searching = false
    }
  }

  function kb(bytes) {
    return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
  }

  onMount(refresh)
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="modal" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="card mem-card" role="dialog" aria-modal="true" aria-label="Memory">
    <div class="head">
      <h2>Memory <span class="muted">· this computer only</span></h2>
      <button class="small" onclick={onclose}>Close</button>
    </div>
    {#if stats}
      <p class="muted stats">
        {stats.chunks} excerpts indexed{stats.first_date ? ` (${fmtDay(stats.first_date)} – ${fmtDay(stats.last_date)})` : ''}
        · {stats.embedded}/{stats.chunks} with meaning vectors ({stats.model})
        · {kb(stats.bytes)}
        {#if stats.excluded}· {stats.excluded} hidden by “forget”{/if}
      </p>
    {/if}
    {#if error}<p class="err">{error}</p>{/if}

    <h3>Search your notes</h3>
    <form class="search" onsubmit={search}>
      <input type="search" bind:value={q} placeholder="e.g. migration, kucing, what I said about Thailand" />
      <input type="date" bind:value={from} title="From (optional)" />
      <input type="date" bind:value={to} title="To (optional)" />
      <button class="primary" disabled={searching || (!q.trim() && !(from && to))}>{searching ? 'Searching…' : 'Search'}</button>
    </form>
    <p class="muted hint">Keywords and meaning (Indonesian and English). Without words, a date range lists that period.</p>
    {#if hits}
      {#if hits.length === 0}<p class="muted">Nothing found.</p>{/if}
      <ul class="hits">
        {#each hits as h (h.id)}
          <li>
            <div class="hit-head">
              <span class="num">{h.when}</span>
              <span class="badge src-{h.source}">{h.label}</span>
              {#if h.keyword}<span class="badge">keyword</span>{/if}
              {#if h.sim != null}<span class="muted num" title="cosine similarity">{h.sim.toFixed(2)}</span>{/if}
            </div>
            <p>{h.text}</p>
          </li>
        {/each}
      </ul>
    {/if}
  </div>
</div>

<style>
  .modal { position: fixed; inset: 0; background: rgb(0 0 0 / 0.7); display: grid; place-items: center; z-index: 2; }
  .mem-card { background: var(--surface); border-radius: 18px; padding: 24px; max-width: 760px; width: 94vw; max-height: 88vh; overflow: auto; }
  .head { display: flex; justify-content: space-between; align-items: center; }
  h2 { font-size: 15px; margin: 0; font-weight: 600; }
  h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); margin: 18px 0 8px; }
  .muted { color: var(--muted); }
  .stats, .hint { font-size: 12.5px; margin: 6px 0; }
  .err { color: var(--rec); }
  .small { font-size: 12px; padding: 4px 10px; }
  .num { font-family: 'Space Grotesk', Inter, system-ui, sans-serif; font-variant-numeric: tabular-nums; }
  .search { display: flex; gap: 8px; flex-wrap: wrap; }
  .search input[type='search'] { flex: 1; min-width: 220px; }
  input {
    font: inherit; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line);
    border-radius: 10px; padding: 7px 10px; color-scheme: dark;
  }
  .hits { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 8px; }
  .hits li { background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
  .hits p { margin: 6px 0 0; line-height: 1.5; font-size: 14px; }
  .hit-head { display: flex; gap: 10px; align-items: center; font-size: 13px; flex-wrap: wrap; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #232321; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .badge.src-note { color: var(--info); }
  .badge.src-reply { color: var(--accent); }
  .badge.src-diary, .badge.src-reflection { color: var(--ok); }
</style>
