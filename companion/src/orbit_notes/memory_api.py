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
