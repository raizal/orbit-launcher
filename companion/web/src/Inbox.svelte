<script>
  // Orbit Inbox (docs/inbox.md): what the companion has for the watch, with read state, and a way to add one.
  import { api, fmtTime } from './api.js'

  let { inbox, onchange, onclose } = $props()
  let title = $state('')
  let body = $state('')
  let sending = $state(false)
  let error = $state('')

  async function run(fn) {
    try {
      error = ''
      await fn()
      await onchange()
    } catch (e) {
      error = e.message
    }
  }

  async function add() {
    sending = true
    await run(async () => {
      await api.addInbox({ title, body })
      title = ''
      body = ''
    })
    sending = false
  }
</script>

<!-- svelte-ignore a11y_click_events_have_key_events -->
<div class="modal" role="presentation" onclick={(e) => e.target === e.currentTarget && onclose()}>
  <div class="card inbox-card" role="dialog" aria-modal="true" aria-label="Inbox">
    <h2>Inbox <span class="muted">· {inbox.unread} unread on the watch</span></h2>
    <p class="muted">The watch checks this on home Wi-Fi (on connect, after uploads, when the face comes on, every 15 min).
      New items buzz and show as a notification; tapping one opens Talk about it.</p>

    <form class="add" onsubmit={(e) => { e.preventDefault(); add() }}>
      <input placeholder="Title (short, spoken-friendly)" maxlength="80" bind:value={title} />
      <input placeholder="Body (optional, a sentence or two)" bind:value={body} />
      <button class="primary" disabled={!title.trim() || sending}>{sending ? 'Sending…' : 'Send to watch'}</button>
    </form>
    {#if error}<p class="err">{error}</p>{/if}

    {#if inbox.items.length === 0}<p class="muted">Nothing yet.</p>{/if}
    <ul class="items">
      {#each inbox.items as i (i.id)}
        <li class:unread={!i.read_at}>
          <div class="head">
            <span class="state" title={i.read_at ? `read ${fmtTime(i.read_at)}` : 'unread'}></span>
            <b>{i.title}</b>
            <span class="badge">{i.kind}</span>
          </div>
          {#if i.body}<p>{i.body}</p>{/if}
          <div class="muted meta">
            <span class="num">{fmtTime(i.created_at)}</span>
            {#if i.ref}<code>{i.ref}</code>{/if}
            <span>{i.read_at ? `read ${fmtTime(i.read_at)}` : 'unread'}</span>
            <button class="small" onclick={() => run(() => api.readInbox(i.id, !i.read_at))}>
              Mark {i.read_at ? 'unread' : 'read'}
            </button>
          </div>
        </li>
      {/each}
    </ul>
    <button onclick={onclose}>Close</button>
  </div>
</div>

<style>
  /* Same look as App.svelte's modals (its styles are scoped there). */
  .modal { position: fixed; inset: 0; background: rgb(0 0 0 / 0.7); display: grid; place-items: center; z-index: 2; }
  .card { background: var(--surface); border-radius: 18px; padding: 24px; }
  h2 { font-size: 15px; margin: 0 0 12px; font-weight: 600; }
  .muted { color: var(--muted); }
  .badge { font-size: 11px; padding: 2px 8px; border-radius: 999px; background: #232321; color: var(--muted); text-transform: uppercase; letter-spacing: 0.06em; }
  .small { font-size: 12px; padding: 4px 10px; }
  input { font: inherit; color: var(--ink); background: var(--surface-2); border: 1px solid var(--line); border-radius: 10px; padding: 8px 12px; }
  .inbox-card { width: min(640px, 92vw); max-height: 86vh; overflow: auto; text-align: left; }
  .add { display: grid; grid-template-columns: 1fr; gap: 8px; margin: 12px 0; }
  .add button { justify-self: start; }
  .items { list-style: none; padding: 0; margin: 12px 0; display: flex; flex-direction: column; gap: 8px; }
  .items li { padding: 10px 12px; border: 1px solid var(--line); border-radius: 10px; background: var(--surface-2); }
  .items li.unread { border-color: var(--accent); }
  .head { display: flex; align-items: center; gap: 8px; }
  .state { width: 8px; height: 8px; border-radius: 50%; background: #3a3a37; flex: none; }
  .unread .state { background: var(--accent); }
  .items p { margin: 6px 0; line-height: 1.45; }
  .meta { display: flex; gap: 12px; align-items: center; font-size: 12px; flex-wrap: wrap; }
  .err { color: var(--rec); }
</style>
