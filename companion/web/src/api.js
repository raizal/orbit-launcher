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
  days: () => call('/days'),
  notes: (day) => call(`/notes?day=${day}`),
  retranscribe: (id) => call(`/notes/${id}/transcribe`, { method: 'POST' }),
  diary: (day) => call(`/diary/${day}`),
  buildDiary: (day) => call(`/diary/${day}`, { method: 'POST' }),
  audioUrl: (id) => `/ui/api/notes/${id}/audio`,
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

export function todayIso() {
  const d = new Date()
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}
