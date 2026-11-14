// Thin client for the companion's local-only /ui/api.
async function call(path, options) {
  const res = await fetch(`/ui/api${path}`, options)
  if (!res.ok) {
    let detail = `${res.status}`
    try { detail = (await res.json()).detail ?? detail } catch {}
    throw new Error(detail)
  }
  return res.json()
}

export const api = {
  status: () => call('/status'),
  talk: () => call('/talk'),
  mood: (days) => call(`/mood?days=${days}`),
  saveTalkSettings: (changes) => call('/talk/settings', {
    method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(changes),
  }),
  // Returns a WAV Blob of a short sample in `voice` at `length_scale` (unsaved values are fine).
  previewVoice: async (voice, length_scale) => {
    const res = await fetch('/ui/api/talk/preview', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ voice, length_scale }),
    })
    if (!res.ok) {
      let detail = `${res.status}`
      try { detail = (await res.json()).detail ?? detail } catch {}
      throw new Error(detail)
    }
    return res.blob()
  },
  memoryStatus: () => call('/memory/status'),
  memorySearch: (q, from, to) => call(`/memory/search?${new URLSearchParams({ q, ...(from ? { from } : {}), ...(to ? { to } : {}) })}`),
  days: () => call('/days'),
  notes: (day) => call(`/notes?day=${day}`),
  retranscribe: (id) => call(`/notes/${id}/transcribe`, { method: 'POST' }),
  diary: (day) => call(`/diary/${day}`),
  buildDiary: (day) => call(`/diary/${day}`, { method: 'POST' }),
  audioUrl: (id) => `/ui/api/notes/${id}/audio`,
  replyUrl: (id) => `/ui/api/notes/${id}/reply.wav`,
  pairingUrl: () => `/ui/api/pairing.png?t=${Date.now()}`,
}

export function fmtDuration(ms) {
  if (ms == null) return '?:??'
  const s = Math.round(ms / 1000)
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export function fmtDay(iso) {
  const d = new Date(`${iso}T12:00:00`)
  return d.toLocaleDateString('en-GB', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })
}

export function fmtTime(iso) {
  if (!iso) return ''
  const d = new Date(iso)
  return d.toLocaleString('en-GB', { day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit' })
}

export function todayIso() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
