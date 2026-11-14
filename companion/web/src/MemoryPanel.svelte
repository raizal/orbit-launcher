<script>
  // Memory (docs/memory.md): search the episodic index; view, edit, retire, delete facts; forget a word.
  // Loopback-only like the rest of /ui/api. SQLite is the source of truth; Obsidian's Memory.md is generated.
  import { onMount } from 'svelte'
  import { api, fmtDay, fmtTime } from './api.js'

  let { onclose } = $props()

  const KINDS = ['fact', 'preference', 'goal', 'decision', 'person', 'place']

  let stats = $state(null)
  let error = $state('')
  let q = $state('')
  let from = $state('')
  let to = $state('')
  let tag = $state('')
  let hits = $state(null)
  let searching = $state(false)

  let facts = $state([])
  let factStatus = $state('active')
  let viewPath = $state('')
  let editing = $state(null) // {id, text, topic, kind}
  let adding = $state({ text: '', topic: '', kind: 'fact' })
  let openSources = $state({})

  let forgetTerm = $state('')
  let forgetPreview = $state(null)
  let forgotten = $state([])
  let tags = $state([])

  async function guard(fn) {
    error = ''
    try {
      await fn()
    } catch (e) {
      error = e.message
    }
  }

  async function refresh() {
    await guard(async () => {
      stats = await api.memoryStatus()
      const f = await api.memoryFacts(factStatus)
      facts = f.facts
      viewPath = f.view
      const g = await api.memoryForgotten()
      forgotten = g.forgotten
      tags = g.tags
    })
  }

  async function search(e) {
    e?.preventDefault()
    searching = true
    await guard(async () => (hits = (await api.memorySearch(q, from, to, tag)).hits))
    searching = false
  }

  function byTag(name) {
    tag = tag === name ? '' : name
    if (q.trim() || tag) search()
  }

  async function saveEdit() {
    const { id, text, topic, kind } = editing
    await guard(async () => {
      await api.memoryEditFact(id, { text, topic, kind })
      editing = null
      await refresh()
    })
  }

  async function setStatus(f, status) {
    await guard(async () => {
      await api.memoryEditFact(f.id, { status })
      await refresh()
    })
  }

  async function remove(f) {
    if (!confirm(`Delete this fact for good?\n\n${f.text}\n\n(Retire keeps it with its history.)`)) return
    await guard(async () => {
      await api.memoryDeleteFact(f.id)
      await refresh()
    })
  }

  async function add(e) {
    e.preventDefault()
    await guard(async () => {
      await api.memoryAddFact(adding)
      adding = { text: '', topic: '', kind: 'fact' }
      await refresh()
    })
  }

  async function previewForget(e) {
    e.preventDefault()
    await guard(async () => (forgetPreview = await api.memoryForget(forgetTerm, true)))
  }

  async function doForget() {
    await guard(async () => {
      await api.memoryForget(forgetTerm, false)
      forgetTerm = ''
      forgetPreview = null
      await refresh()
    })
  }

  async function unforget(f) {
    await guard(async () => {
      await api.memoryUnforget(f.id)
      await refresh()
    })
  }

  function kb(bytes) {
    return bytes > 1e6 ? `${(bytes / 1e6).toFixed(1)} MB` : `${Math.round(bytes / 1024)} KB`
  }

  let grouped = $derived.by(() => {
    const g = {}
    for (const f of facts) (g[f.topic] ??= []).push(f)
    return Object.entries(g).sort((a, b) => a[0].localeCompare(b[0]))
  })

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
    {#if tags.length}
      <div class="tags">
        {#each tags.slice(0, 40) as t (t.name)}
          <button class="chip" class:on={tag === t.name} onclick={() => byTag(t.name)} title="{t.kind} · {t.notes} notes · last {t.last}">
            {t.name} <span class="muted">{t.notes}</span>
          </button>
        {/each}
      </div>
      {#if tag}<p class="muted hint">Only notes mentioning <b>{tag}</b>.</p>{/if}
    {/if}
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

    <div class="facts-head">
      <h3>Facts Orbit remembers</h3>
      <select bind:value={factStatus} onchange={refresh}>
        <option value="active">current</option>
        <option value="corrected">corrected</option>
        <option value="retired">retired</option>
        <option value="all">all</option>
      </select>
    </div>
    <p class="muted hint">Distilled from your talk turns and diary builds. Edits apply to the very next Talk turn.
      Obsidian view (generated, read-only): <code>{viewPath}</code></p>
    {#if facts.length === 0}<p class="muted">None.</p>{/if}
    {#each grouped as [topic, list] (topic)}
      <div class="topic">
        <div class="topic-name">{topic}</div>
        <ul class="facts">
          {#each list as f (f.id)}
            <li class:inactive={f.status !== 'active'}>
              {#if editing?.id === f.id}
                <div class="edit">
                  <textarea rows="2" bind:value={editing.text}></textarea>
                  <div class="actions">
                    <input bind:value={editing.topic} placeholder="topic" />
                    <select bind:value={editing.kind}>{#each KINDS as k}<option>{k}</option>{/each}</select>
                    <button class="primary small" onclick={saveEdit}>Save</button>
                    <button class="small" onclick={() => (editing = null)}>Cancel</button>
                  </div>
                </div>
              {:else}
                <div class="fact-row">
                  <span class="fact-text">{f.text}</span>
                  <span class="badge">{f.kind}</span>
                  {#if f.status !== 'active'}<span class="badge st-{f.status}">{f.status}</span>{/if}
                </div>
                <div class="fact-meta muted">
                  <span class="num">{f.id}</span> · first heard {fmtDay(f.first_seen)}
                  {#if f.last_confirmed !== f.first_seen}· confirmed {fmtDay(f.last_confirmed)}{/if}
                  · {f.confidence}
                  <button class="link" onclick={() => (openSources[f.id] = !openSources[f.id])}>
                    {f.sources.length} source{f.sources.length === 1 ? '' : 's'} · history
                  </button>
                </div>
                {#if openSources[f.id]}
                  <ul class="sources">
                    {#each f.sources as s}
                      <li><span class="num">{s.started_at ? fmtTime(s.started_at) : s.local_date}</span> <span class="muted">({s.via})</span>
                        {#if s.text}— “{s.text}”{/if}</li>
                    {/each}
                    {#each f.history as h}
                      <li class="muted"><span class="num">{fmtTime(h.at)}</span> {h.event}{h.via ? ` via ${h.via}` : ''}
                        {#if h.by}→ {h.by}{/if}{#if h.replaces}(replaces {h.replaces}){/if}{#if h.corrects}(corrects {h.corrects}){/if}
                        {#if h.term}(“{h.term}”){/if}{#if h.before?.text}(was: “{h.before.text}”){/if}</li>
                    {/each}
                  </ul>
                {/if}
                <div class="actions">
                  <button class="small" onclick={() => (editing = { id: f.id, text: f.text, topic: f.topic, kind: f.kind })}>Edit</button>
                  {#if f.status === 'active'}
                    <button class="small" onclick={() => setStatus(f, 'retired')}>Retire</button>
                  {:else}
                    <button class="small" onclick={() => setStatus(f, 'active')}>Make current</button>
                  {/if}
                  <button class="small danger" onclick={() => remove(f)}>Delete</button>
                </div>
              {/if}
            </li>
          {/each}
        </ul>
      </div>
    {/each}

    <form class="add" onsubmit={add}>
      <input bind:value={adding.text} placeholder="Add a fact, e.g. “His cat is called Mochi.”" />
      <input class="topic-in" bind:value={adding.topic} placeholder="topic" />
      <select bind:value={adding.kind}>{#each KINDS as k}<option>{k}</option>{/each}</select>
      <button disabled={!adding.text.trim() || !adding.topic.trim()}>Add</button>
    </form>

    <h3>Forget</h3>
    <p class="muted hint">Retires matching facts and hides matching excerpts from recall, now and in future notes.
      Your recordings and transcripts stay.</p>
    <form class="search" onsubmit={previewForget}>
      <input bind:value={forgetTerm} placeholder="a word or phrase" />
      <button disabled={forgetTerm.trim().length < 2}>Check</button>
    </form>
    {#if forgetPreview}
      <div class="preview">
        Would retire {forgetPreview.facts.length} fact{forgetPreview.facts.length === 1 ? '' : 's'}
        {#if forgetPreview.facts.length}({forgetPreview.facts.map((f) => `“${f.text}”`).join('; ')}){/if}
        and hide {forgetPreview.chunks} excerpt{forgetPreview.chunks === 1 ? '' : 's'}.
        <button class="small danger" onclick={doForget}>Forget “{forgetTerm}”</button>
      </div>
    {/if}
    {#if forgotten.length}
      <ul class="forgotten">
        {#each forgotten as f (f.id)}
          <li>“{f.term}” <span class="muted">· {fmtTime(f.created_at)} · {f.facts} facts, {f.chunks} excerpts</span>
            <button class="small" onclick={() => unforget(f)} title="Excerpts become searchable again; retired facts stay retired">Undo</button></li>
        {/each}
      </ul>
    {/if}
  </div>
</div>

<style>
  .modal { position: fixed; inset: 0; background: rgb(0 0 0 / 0.7); display: grid; place-items: center; z-index: 2; }
  .mem-card { background: var(--surface); border-radius: 18px; padding: 24px; max-width: 780px; width: 94vw; max-height: 88vh; overflow: auto; }
  .head, .facts-head { display: flex; justify-content: space-between; align-items: center; gap: 12px; }
  h2 { font-size: 15px; margin: 0; font-weight: 600; }
  h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); margin: 20px 0 8px; }
  .muted { color: var(--muted); }
  .stats, .hint { font-size: 12.5px; margin: 6px 0; }
  .hint code { font-size: 11.5px; word-break: break-all; }
  .err { color: var(--rec); }
  .small { font-size: 12px; padding: 4px 10px; }
  .danger { color: var(--rec); }
  .link { background: none; border: none; padding: 0; color: var(--info); font-size: 12px; }
  .num { font-family: 'Space Grotesk', Inter, system-ui, sans-serif; font-variant-numeric: tabular-nums; }
  .search, .add { display: flex; gap: 8px; flex-wrap: wrap; }
  .search input:first-child, .add input:first-child { flex: 1; min-width: 220px; }
  .topic-in { width: 140px; }
  input, select, textarea {
    font: inherit; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line);
    border-radius: 10px; padding: 7px 10px; color-scheme: dark;
  }
  textarea { width: 100%; resize: vertical; }
  .tags { display: flex; flex-wrap: wrap; gap: 6px; margin: 8px 0; }
  .chip { font-size: 12px; padding: 3px 10px; }
  .chip.on { border-color: var(--accent); color: var(--accent); }
  .hits, .facts, .sources, .forgotten { list-style: none; padding: 0; margin: 8px 0 0; display: flex; flex-direction: column; gap: 8px; }
  .hits li, .facts > li { background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px; padding: 10px 12px; }
  .hits p { margin: 6px 0 0; line-height: 1.5; font-size: 14px; }
  .hit-head, .fact-row { display: flex; gap: 10px; align-items: center; font-size: 13px; flex-wrap: wrap; }
  .fact-text { font-size: 14px; flex: 1; min-width: 200px; }
  .fact-meta { font-size: 12px; margin: 4px 0 6px; }
  .facts > li.inactive { opacity: 0.6; }
  .sources { gap: 4px; font-size: 12.5px; margin: 0 0 8px; }
  .topic { margin: 10px 0; }
  .topic-name { font-weight: 600; font-size: 13px; }
  .actions { display: flex; gap: 6px; align-items: center; flex-wrap: wrap; }
  .edit { display: flex; flex-direction: column; gap: 6px; }
  .add { margin-top: 12px; }
  .preview { border: 1px solid var(--accent); border-radius: 10px; padding: 10px 12px; margin-top: 8px; font-size: 13px; }
  .forgotten li { font-size: 13px; display: flex; gap: 8px; align-items: center; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #232321; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .badge.src-note { color: var(--info); }
  .badge.src-reply { color: var(--accent); }
  .badge.src-diary, .badge.src-reflection { color: var(--ok); }
  .badge.st-corrected, .badge.st-retired { color: var(--rec); }
</style>
