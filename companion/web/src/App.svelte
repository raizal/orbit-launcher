<script>
  import { onMount } from 'svelte'
  import { api, fmtDay, fmtDuration, fmtTime, todayIso } from './api.js'
  import { renderDiary } from './markdown.js'

  let status = $state(null)
  let days = $state([])
  let day = $state(null)
  let notes = $state([])
  let diary = $state(null) // { path, markdown }
  let building = $state(false)
  let showPair = $state(false)
  let showSource = $state(false)
  let talk = $state(null) // { workspace, pending, audit, settings, voices }
  let showTalk = $state(false)
  // Talk settings form: a draft edited locally, saved with PUT /talk/settings.
  let draft = $state(null) // { voice, length_scale, reply_length, personality }
  let saving = $state(false)
  let savedMsg = $state('')
  let previewing = $state(null) // voice name being synthesized
  let playing = $state(null) // voice name whose preview is playing
  let player = null
  const SETTING_KEYS = ['voice', 'length_scale', 'reply_length', 'personality']
  const changes = $derived(draft && talk ? Object.fromEntries(SETTING_KEYS
    .filter((k) => (k === 'personality' ? draft[k].trim().split(/\s+/).join(' ') : draft[k]) !== talk.settings[k])
    .map((k) => [k, draft[k]])) : {})
  const dirty = $derived(Object.keys(changes).length > 0)
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
    const wasDirty = dirty
    ;[status, days, talk] = await Promise.all([api.status(), api.days(), api.talk()])
    // Follow changes made meanwhile (e.g. confirmed by voice on the watch) unless the form has unsaved edits.
    if (draft && !wasDirty) draft = pickSettings(talk.settings)
    if (!day) day = days[0]?.date ?? todayIso()
  }

  async function loadDay() {
    if (!day) return
    const d = day
    const [n, p] = await Promise.all([api.notes(d), api.diary(d)])
    if (d === day) {
      // Newest first for review; the API (and the diary page) keep time order.
      notes = [...n].sort((a, b) => b.started_at.localeCompare(a.started_at))
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

  function openTalk() {
    draft = talk ? { ...pickSettings(talk.settings) } : null
    savedMsg = ''
    showTalk = true
  }

  function closeTalk() {
    stopPreview()
    showTalk = false
  }

  function pickSettings(s) {
    return Object.fromEntries(SETTING_KEYS.map((k) => [k, s[k] ?? '']))
  }

  async function saveSettings(c = changes) {
    saving = true
    savedMsg = ''
    await guard(async () => {
      const s = await api.saveTalkSettings(c)
      talk = { ...talk, settings: s }
      // Only the saved keys: other unsaved edits in the form stay (e.g. after "Reset personality").
      for (const k of Object.keys(c)) draft[k] = s[k] ?? ''
      savedMsg = 'Saved. The next Talk reply uses these settings.'
    })
    saving = false
  }

  function resetPersonality() {
    if (talk.settings.personality && !confirm('Remove your personality instructions and go back to the base persona?')) return
    saveSettings({ personality: '' })
  }

  function stopPreview() {
    player?.pause()
    if (player?.src) URL.revokeObjectURL(player.src)
    player = null
    playing = null
  }

  async function preview(voice) {
    stopPreview()
    previewing = voice
    await guard(async () => {
      const blob = await api.previewVoice(voice, Number(draft.length_scale))
      player = new Audio(URL.createObjectURL(blob))
      player.onended = stopPreview
      playing = voice
      await player.play()
      // Mark the voice as downloaded (the preview fetched it if needed).
      if (talk.voices[voice]) talk.voices[voice].installed = true
    })
    previewing = null
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

<svelte:window onkeydown={(e) => e.key === 'Escape' && ((showPair = false), closeTalk())} />

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
      {#if status.app}
        <span class="muted" title={`Published ${status.app.published_at} · sha256 ${status.app.sha256}`}>
          watch app {status.app.version_name} (#{status.app.version_code})
        </span>
      {:else}<span class="muted">no watch app published</span>{/if}
    </div>
  {/if}
  <button onclick={openTalk}>
    Talk assistant{#if talk?.pending && !talk.pending.expired}<span class="pending-dot" title="An action waits for confirmation"></span>{/if}
  </button>
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
          {#if n.mode === 'talk'}<span class="badge talk">Talk</span>{/if}
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
        {#if n.mode === 'talk'}
          <div class="reply">
            <div class="reply-head">
              <span class="label">Feedback</span>
              {#if n.talk_status !== 'done'}<span class="badge {n.talk_status}">{n.talk_status}</span>{/if}
            </div>
            {#if n.reply}<p>{n.reply}</p>
            {:else if n.talk_status === 'error'}<p class="empty">{n.talk_error}</p>
            {:else}<p class="empty">Working on it…</p>{/if}
            {#if n.reply_audio}
              <audio controls preload="none" src={api.replyUrl(n.id)}></audio>
            {/if}
          </div>
        {/if}
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

{#if showTalk && talk}
  <!-- svelte-ignore a11y_click_events_have_key_events -->
  <div class="modal" role="presentation" onclick={(e) => e.target === e.currentTarget && closeTalk()}>
    <div class="card talk-card" role="dialog" aria-modal="true" aria-label="Talk assistant">
      <h2>Talk assistant</h2>
      <p class="muted">BB workspace: {talk.workspace === 'bb' ? 'connected (read on demand, actions need your spoken yes)' : 'off'}</p>

      <h3>Waiting for confirmation</h3>
      {#if talk.pending}
        <div class="pending" class:expired={talk.pending.expired}>
          <div><span class="badge">{talk.pending.kind}</span> {talk.pending.summary}</div>
          <pre>{JSON.stringify(talk.pending.params, null, 1)}</pre>
          <p class="muted small-print">
            Proposed {fmtTime(talk.pending.created_at)} ·
            {talk.pending.expired ? 'expired' : `expires ${fmtTime(talk.pending.expires_at)}`}.
            Confirm or cancel by voice on the watch (“yes” / “no”) — not from here.
          </p>
        </div>
      {:else}
        <p class="muted">Nothing pending.</p>
      {/if}

      <h3>Voice &amp; personality</h3>
      {#if draft}
        <div class="settings">
          <div class="field">
            <span class="field-label">Voice</span>
            <ul class="voices">
              {#each Object.entries(talk.voices) as [name, v] (name)}
                <li class:selected={draft.voice === name}>
                  <label>
                    <input type="radio" name="voice" value={name} bind:group={draft.voice} />
                    <span class="voice-name">{name}</span>
                    <span class="muted">{v.label}{v.installed ? '' : ' · downloads on first use'}</span>
                  </label>
                  {#if playing === name}
                    <button class="small" onclick={stopPreview}>■ Stop</button>
                  {:else}
                    <button class="small" onclick={() => preview(name)} disabled={previewing !== null}>
                      {previewing === name ? 'Loading…' : '▶ Preview'}
                    </button>
                  {/if}
                </li>
              {/each}
            </ul>
          </div>

          <label class="field">
            <span class="field-label">Speaking rate <b class="num">{Number(draft.length_scale).toFixed(2)}</b>
              <span class="muted">(Piper length scale; higher = slower; previews use it)</span></span>
            <input type="range" min={talk.limits.length_scale[0]} max={talk.limits.length_scale[1]} step="0.05"
                   bind:value={draft.length_scale} />
          </label>

          <label class="field">
            <span class="field-label">Reply length</span>
            <select bind:value={draft.reply_length}>
              {#each Object.entries(talk.limits.reply_lengths) as [k, desc]}
                <option value={k}>{k} — {desc}</option>
              {/each}
            </select>
          </label>

          <label class="field">
            <span class="field-label">Personality / style
              <span class="muted">(layered on the base persona; the fixed rules always stay)</span></span>
            <textarea rows="4" maxlength={talk.limits.personality} bind:value={draft.personality}
                      placeholder="Empty = base persona only. E.g. “Cheerful and a bit playful; call me Raiz.”"></textarea>
            <span class="muted small-print">{draft.personality.length} / {talk.limits.personality}</span>
          </label>
          <details class="persona">
            <summary>Base persona (fixed)</summary>
            <pre>{talk.base_persona}</pre>
          </details>

          <div class="actions">
            <button class="primary" onclick={() => saveSettings()} disabled={!dirty || saving}>
              {saving ? 'Saving…' : 'Save'}
            </button>
            <button onclick={() => (draft = pickSettings(talk.settings))} disabled={!dirty || saving}>Revert</button>
            <button onclick={resetPersonality} disabled={saving || !talk.settings.personality}>Reset personality to base persona</button>
            {#if savedMsg && !dirty}<span class="ok">{savedMsg}</span>{/if}
          </div>
          {#if talk.settings.updated_at}
            <p class="muted small-print">Last changed {fmtTime(talk.settings.updated_at)}. You can also ask on the watch, e.g. “speak a bit slower”.</p>
          {/if}
        </div>
      {/if}

      <h3>Action log</h3>
      {#if talk.audit.length === 0}<p class="muted">No actions yet.</p>{/if}
      <ul class="audit">
        {#each talk.audit as e}
          <li>
            <span class="num muted">{fmtTime(e.at)}</span>
            <span class="badge ev-{e.event}">{e.event}</span>
            <span>{e.kind ?? ''}</span>
            <span>{e.summary ?? ''}</span>
            {#if e.result_text}<div class="muted">{e.result_text}</div>{/if}
            {#if e.error}<div class="err">{e.error}</div>{/if}
            {#if e.argv}<code>{e.argv.join(' ')}</code>{/if}
          </li>
        {/each}
      </ul>
      <button onclick={closeTalk}>Close</button>
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
  .badge.talk { color: var(--accent); border: 1px solid var(--accent); background: transparent; }
  .badge.queued, .badge.transcribing, .badge.thinking, .badge.speaking { color: var(--info); }
  .reply { margin-top: 12px; padding: 10px 12px; border-left: 3px solid var(--accent); background: var(--surface); border-radius: 8px; }
  .reply-head { display: flex; gap: 10px; align-items: center; margin-bottom: 4px; }
  .reply .label { font-size: 12px; color: var(--accent); text-transform: uppercase; letter-spacing: 0.06em; }
  .reply p { margin: 4px 0 8px; line-height: 1.5; }
  .reply .empty { color: var(--muted); font-style: italic; }
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
  .pending-dot { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: var(--rec); margin-left: 8px; vertical-align: middle; }
  .talk-card { max-width: 640px; width: 92vw; max-height: 86vh; overflow: auto; text-align: left; }
  .talk-card h3 { font-size: 13px; text-transform: uppercase; letter-spacing: 0.06em; color: var(--accent); margin: 18px 0 8px; }
  .pending { border: 1px solid var(--accent); border-radius: 10px; padding: 10px 12px; }
  .pending.expired { border-color: var(--line); opacity: 0.6; }
  .pending pre { margin: 8px 0; font-size: 12px; }
  .audit { list-style: none; padding: 0; margin: 0 0 16px; display: flex; flex-direction: column; gap: 8px; font-size: 13px; }
  .audit li { border-bottom: 1px solid var(--line); padding-bottom: 6px; display: flex; flex-wrap: wrap; gap: 8px; align-items: baseline; }
  .audit li div, .audit li code { flex-basis: 100%; }
  .audit code { font-size: 11.5px; word-break: break-all; }
  .audit .err { color: var(--rec); }
  .badge.ev-executed, .badge.ev-changed { color: var(--ok); }
  .settings { display: flex; flex-direction: column; gap: 14px; }
  .field { display: flex; flex-direction: column; gap: 6px; font-size: 13px; }
  .field-label { font-weight: 600; }
  .field-label .muted { font-weight: 400; }
  .voices { list-style: none; padding: 0; margin: 0; display: flex; flex-direction: column; gap: 4px; }
  .voices li { display: flex; align-items: center; gap: 10px; padding: 6px 10px; border: 1px solid var(--line); border-radius: 10px; }
  .voices li.selected { border-color: var(--accent); }
  .voices label { display: flex; align-items: baseline; gap: 10px; flex: 1; min-width: 0; cursor: pointer; }
  .voices input { accent-color: var(--accent); align-self: center; }
  .voice-name { font-family: 'Space Grotesk', Inter, sans-serif; }
  input[type='range'] { accent-color: var(--accent); width: 100%; }
  select, textarea {
    font: inherit; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line);
    border-radius: 10px; padding: 8px 10px;
  }
  textarea { resize: vertical; line-height: 1.45; }
  .persona summary { cursor: pointer; color: var(--muted); font-size: 13px; }
  .persona pre { font-size: 12px; }
  .actions { display: flex; gap: 8px; align-items: center; flex-wrap: wrap; }
  .ok { color: var(--ok); font-size: 13px; }
  .badge.ev-failed, .badge.ev-rejected { color: var(--rec); }
  .badge.ev-proposed, .badge.ev-confirmed { color: var(--info); }

  @media (max-width: 1000px) {
    main { grid-template-columns: 1fr; }
    aside { position: static; max-height: none; }
  }
</style>
