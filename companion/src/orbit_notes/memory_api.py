"""Loopback-only web UI API for memory (docs/memory.md): search, facts, forget. Mounted by ui.mount_ui."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Body, HTTPException, Query

from .memory import Invalid, Memory


def _day(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        raise HTTPException(422, "dates must be YYYY-MM-DD") from None


def mount_memory_api(api: APIRouter, memory: Memory) -> None:
    @api.get("/memory/search")
    def memory_search(q: str = Query("", max_length=500), limit: int = Query(20, ge=1, le=100),
                      date_from: str | None = Query(None, alias="from"), date_to: str | None = Query(None, alias="to"),
                      tag: str | None = None):
        """Hybrid search over the episodic index (keyword + meaning), best first, every hit with its date.
        Without a query but with a date range: everything in that range."""
        a, b = _day(date_from), _day(date_to)
        if q.strip():
            hits = memory.search(q, limit=limit, date_from=a, date_to=b, tag=tag)
        elif a and b:
            hits = memory.in_range(a, b, limit=limit)
        else:
            hits = []
        return {"query": q, "hits": [h.json() for h in hits]}

    @api.get("/memory/status")
    def memory_status():
        return memory.stats()

    def fact_json(f) -> dict:
        """A fact with its sources resolved to time + a snippet of what was said (for the Memory panel)."""
        out = f.json()
        srcs = []
        for s in f.sources:
            note = memory.store.get(s["note_id"]) if s.get("note_id") and memory.store else None
            text = None
            if note:
                p = memory.store.audio_path(note).with_suffix(".txt")
                text = " ".join(p.read_text().split())[:240] if p.exists() else None
            srcs.append({**s, "started_at": note.started_at if note else None, "text": text})
        out["sources"] = srcs
        return out

    def invalid(e: Exception):
        raise HTTPException(422, str(e)) from None

    @api.get("/memory/facts")
    def memory_facts(status: str = "active"):
        if status not in ("active", "corrected", "retired", "all"):
            raise HTTPException(422, "status must be active, corrected, retired or all")
        return {"facts": [fact_json(f) for f in memory.facts(None if status == "all" else status)],
                "view": str(memory.view_path())}

    @api.post("/memory/facts")
    def memory_fact_add(body: dict = Body(...)):
        try:
            return fact_json(memory.add_fact(body.get("text"), body.get("topic"), body.get("kind") or "fact"))
        except Invalid as e:
            invalid(e)

    @api.put("/memory/facts/{fact_id}")
    def memory_fact_edit(fact_id: str, body: dict = Body(...)):
        """Owner edits from this computer: applied directly (history kept), removed from prompts right away."""
        if not memory.fact(fact_id):
            raise HTTPException(404, "no such fact")
        try:
            return fact_json(memory.edit_fact(fact_id, body))
        except Invalid as e:
            invalid(e)

    @api.delete("/memory/facts/{fact_id}")
    def memory_fact_delete(fact_id: str):
        if not memory.delete_fact(fact_id):
            raise HTTPException(404, "no such fact")
        return {"deleted": fact_id}

    @api.get("/memory/forgotten")
    def memory_forgotten():
        return {"forgotten": memory.forgotten(), "tags": memory.tag_names()}

    @api.post("/memory/forget")
    def memory_forget(body: dict = Body(...)):
        """Retire matching facts and hide matching excerpts from recall (now and in future notes). The notes
        themselves are never deleted. `{"preview": true}` only counts."""
        try:
            if body.get("preview"):
                facts, n = memory.forget_preview(body.get("query"))
                return {"facts": [f.json() for f in facts], "chunks": n}
            n_facts, n_chunks = memory.forget(body.get("query"))
        except Invalid as e:
            invalid(e)
        return {"facts": n_facts, "chunks": n_chunks}

    @api.delete("/memory/forgotten/{forget_id}")
    def memory_unforget(forget_id: int):
        if not memory.unforget(forget_id):
            raise HTTPException(404, "no such entry")
        return {"restored": forget_id}

    @api.post("/memory/view")
    def memory_view():
        return {"path": str(memory.write_view())}
