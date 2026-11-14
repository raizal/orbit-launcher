<script>
  import { onMount } from 'svelte'
  import { api, fmtDay, fmtDuration, todayIso } from './api.js'
  import { renderDiary } from './markdown.js'

  let status = $state(null)
  let days = $state([])
  let day = $state(null)
  let notes = $state([])
  let diary = $state(null) // { path, markdown }
  let building = $state(false)
  let showPair = $state(false)
  let showSource = $state(false)
  const page = $derived(diary?.markdown ? renderDiary(diary.markdown) : null)
  let error = $state('')

  const waiting = $derived(notes.filter((n) => n.transcript_status !== 'done' && n.transcript_status !== 'error').length)

  async function guard(fn) {
    try {
      error = ''
      await fn()
    } catch (e) {
      error = e.message
    }
  }

  async function loadOverview() {
    ;[status, days] = await Promise.all([api.status(), api.days()])
    if (!day) day = days[0]?.date ?? todayIso()
  }

  async function loadDay() {
    if (!day) return
    const d = day
    const [n, p] = await Promise.all([api.notes(d), api.diary(d)])
    if (d === day) {
      notes = n
      diary = p
    }
  }

  function pick(d) {
    day = d
    notes = []
    diary = null
    guard(loadDay)
  }

  async function buildDiary() {
    building = true
    await guard(async () => (diary = await api.buildDiary(day)))
    building = false
  }

  async function retranscribe(id) {
    await guard(async () => {
      await api.retranscribe(id)
      await loadDay()
    })
  }

  onMount(() => {
    guard(async () => {
      await loadOverview()
      await loadDay()
    })
    // Keep counts and transcripts fresh while notes arrive / get transcribed.
    const timer = setInterval(() => guard(async () => {
      await loadOverview()
      await loadDay()
    }), 5000)
    return () => clearInterval(timer)
  })
</script>

<svelte:window onkeydown={(e) => e.key === 'Escape' && (showPair = false)} />

<header>
  <div class="brand">
    <span class="dot"></span>
    <h1>Orbit Notes</h1>
    {#if status}<span class="chip">{status.name}</span>{/if}
  </div>
  {#if status}
    <div class="stats">
      <span><b class="num">{status.notes}</b> notes</span>
      <span class:busy={status.pending_transcripts > 0}>
        <b class="num">{status.pending_transcripts}</b> to transcribe
      </span>
      <span class="muted">whisper {status.whisper_model} · diary LLM {status.llm_provider}</span>
      {#if !status.worker}<span class="warn">transcription worker off</span>{/if}
    </div>
  {/if}
  <button onclick={() => (showPair = true)}>Pair watch</button>
</header>

{#if error}<div class="error">{error}</div>{/if}

<main>
  <nav>
    <h2>Days</h2>
    {#if days.length === 0}<p class="muted">No notes yet. Record one on the watch.</p>{/if}
    {#each days as d (d.date)}
      <button class="day" class:active={d.date === day} onclick={() => pick(d.date)}>
        <span>{fmtDay(d.date)}</span>
        <span class="count num">{d.count}</span>
      </button>
    {/each}
  </nav>

  <section class="notes">
    {#if day}
      <h2>{fmtDay(day)} <span class="muted">· {notes.length} notes{waiting ? ` · ${waiting} transcribing` : ''}</span></h2>
    {/if}
    {#each notes as n (n.id)}
      <article>
        <div class="row">
          <span class="time num">{n.time}</span>
          <span class="muted num">{fmtDuration(n.duration_ms)}</span>
          <span class="badge {n.transcript_status}">{n.transcript_status}</span>
          <span class="stem muted">{n.stem}</span>
          <button class="small" onclick={() => retranscribe(n.id)} disabled={!status?.worker}>Re-transcribe</button>
        </div>
        <audio controls preload="none" src={api.audioUrl(n.id)}></audio>
        <p class="transcript" class:empty={!n.transcript}>
          {#if n.transcript}{n.transcript}
          {:else if n.transcript === ''}(no speech)
          {:else}Not transcribed yet{/if}
        </p>
      </article>
    {/each}
  </section>

  <aside>
    <div class="diary-head">
      <h2>Diary page</h2>
      <button class="primary" onclick={buildDiary} disabled={!day || building}>
        {building ? 'Building…' : diary?.markdown ? 'Rebuild' : 'Build'}
      </button>
    </div>
    {#if page}
      <p class="muted path">
        {diary.path}
        <button class="small" onclick={() => (showSource = !showSource)}>{showSource ? 'Rendered' : 'Source'}</button>
      </p>
      {#if showSource}
        <pre>{diary.markdown}</pre>
      {:else}
        {#if page.meta.length}
          <dl class="meta">
            {#each page.meta as m}<dt>{m.key}</dt><dd>{m.value}</dd>{/each}
          </dl>
        {/if}
        <div class="md">{@html page.html}</div>
      {/if}
    {:else}
      <p class="muted">No page for this day yet.</p>
    {/if}
  </aside>
</main>

{#if showPair}
  <!-- Backdrop click closes; Escape is handled on the window. -->
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="modal" role="presentation" onclick={(e) => e.target === e.currentTarget && (showPair = false)}>
    <div class="card" role="dialog" aria-modal="true" aria-label="Pair the watch">
      <h2>Pair the watch</h2>
      <p class="muted">On the watch: open the inbox → tap the bottom label → <b>Pair: scan QR</b>. The watch must be on this home Wi-Fi.</p>
      <img src={api.pairingUrl()} alt="Pairing QR code" />
      <p class="muted small-print">Contains the server token. Don't share a screenshot.</p>
      <button onclick={() => (showPair = false)}>Close</button>
    </div>
  </div>
{/if}

<style>
  header {
    display: flex; align-items: center; gap: 24px; padding: 16px 28px;
    border-bottom: 1px solid var(--line); position: sticky; top: 0; background: var(--bg); z-index: 1;
  }
  .brand { display: flex; align-items: center; gap: 10px; }
  .dot { width: 12px; height: 12px; border-radius: 50%; background: var(--accent); }
  h1 { font-family: 'Space Grotesk', Inter, sans-serif; font-size: 20px; margin: 0; }
  .chip { font-size: 12px; padding: 2px 10px; border-radius: 999px; background: var(--surface); color: var(--muted); }
  .stats { display: flex; gap: 18px; flex: 1; font-size: 14px; flex-wrap: wrap; }
  .busy b { color: var(--info); }
  .warn { color: var(--rec); }
  .muted { color: var(--muted); }
  .error { margin: 12px 28px 0; padding: 10px 14px; border-radius: 10px; background: #2a1513; color: var(--rec); }

  main { display: grid; grid-template-columns: 220px minmax(0, 1fr) minmax(0, 420px); gap: 24px; padding: 20px 28px; }
  h2 { font-size: 15px; margin: 0 0 12px; font-weight: 600; }

  nav { display: flex; flex-direction: column; gap: 6px; }
  .day { display: flex; justify-content: space-between; border-radius: 10px; text-align: left; background: transparent; border-color: transparent; }
  .day.active { background: var(--surface); border-color: var(--line); }
  .count { color: var(--muted); }

  .notes { display: flex; flex-direction: column; gap: 12px; }
  article { background: var(--surface-2); border: 1px solid var(--line); border-radius: 14px; padding: 14px 16px; }
  .row { display: flex; align-items: center; gap: 12px; margin-bottom: 10px; }
  .time { font-size: 18px; font-weight: 600; }
  .stem { font-size: 12px; flex: 1; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #232321; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .badge.done { color: var(--ok); }
  .badge.pending { color: var(--info); }
  .badge.error { color: var(--rec); }
  .small { font-size: 12px; padding: 4px 10px; }
  audio { width: 100%; height: 36px; }
  .transcript { margin: 10px 0 0; line-height: 1.5; white-space: pre-wrap; }
  .transcript.empty { color: var(--muted); font-style: italic; }

  aside { position: sticky; top: 80px; align-self: start; max-height: calc(100vh - 100px); overflow: auto; }
  .diary-head { display: flex; justify-content: space-between; align-items: center; margin-bottom: 8px; }
  .path { font-size: 12px; word-break: break-all; }
  pre { white-space: pre-wrap; background: var(--surface-2); border: 1px solid var(--line); border-radius: 12px; padding: 14px; font-size: 12.5px; line-height: 1.5; }

  .path { display: flex; gap: 8px; align-items: center; justify-content: space-between; }
  .meta { display: grid; grid-template-columns: auto 1fr; gap: 2px 12px; margin: 0 0 12px; padding: 8px 12px; font-size: 12px; border: 1px solid var(--line); border-radius: 10px; }
  .meta dt { color: var(--muted); }
  .meta dd { margin: 0; }
  .md { line-height: 1.55; font-size: 14px; }
  .md :global(h1) { font-family: 'Space Grotesk', Inter, sans-serif; font-size: 22px; margin: 4px 0 10px; }
  .md :global(h2) { font-size: 16px; margin: 20px 0 8px; color: var(--accent); }
  .md :global(ul) { padding-left: 18px; }
  .md :global(li) { margin: 6px 0; }
  .md :global(li audio) { display: block; width: 100%; height: 32px; margin: 4px 0; }
  .md :global(em) { color: var(--muted); }
  .md :global(code) { background: var(--surface); padding: 1px 5px; border-radius: 5px; font-size: 12.5px; }
  .md :global(.wikilink) { color: var(--info); }
  .md :global(details.callout) { border-left: 3px solid var(--line); background: var(--surface-2); border-radius: 6px; padding: 6px 12px; margin: 8px 0; }
  .md :global(details.callout summary) { cursor: pointer; color: var(--muted); font-size: 13px; }
  .md :global(details.callout p) { margin: 6px 0 2px; }

  .modal { position: fixed; inset: 0; background: rgb(0 0 0 / 0.7); display: grid; place-items: center; z-index: 2; }
  .card { background: var(--surface); border-radius: 18px; padding: 24px; max-width: 360px; text-align: center; }
  .card img { width: 260px; height: 260px; border-radius: 12px; background: #fff; margin: 8px 0; }
  .small-print { font-size: 12px; }

  @media (max-width: 1000px) {
    main { grid-template-columns: 1fr; }
    aside { position: static; max-height: none; }
  }
</style>
