"""Inbox routes (docs/inbox.md): `/v1/inbox` for the watch (bearer token), `/ui/api/inbox` for this computer."""

from __future__ import annotations

from fastapi import APIRouter, Body, Depends, FastAPI, HTTPException, Query

from .inbox import Inbox, InvalidItem, NewItem, Producers


def mount_inbox(app: FastAPI, require_token, inbox: Inbox, producers: Producers | None) -> None:
    router = APIRouter(prefix="/v1/inbox", dependencies=[Depends(require_token)])

    @router.get("")
    def changes(since: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
        """Items added or whose read state changed after `since` (newest first) + the new cursor.
        Producers run here, lazily (at most once a minute)."""
        if producers:
            producers.refresh()
        cursor, items = inbox.changes(since, limit)
        return {"cursor": cursor, "unread": len(inbox.unread()), "items": [i.public() for i in items]}

    @router.post("/{item_id}/read")
    def read(item_id: str):
        item = inbox.mark_read(item_id)
        if not item:
            raise HTTPException(404, "no such inbox item")
        return item.public()

    app.include_router(router)


def mount_inbox_ui(api: APIRouter, inbox: Inbox, producers: Producers | None) -> None:
    """Loopback-only (the router `api` carries require_local). POST is how scripts and the orchestrator
    notify the watch: `curl -X POST localhost:8765/ui/api/inbox -H 'content-type: application/json'
    -d '{"title": "…", "body": "…"}'`."""

    @api.get("/inbox")
    def ui_list(limit: int = Query(100, ge=1, le=200)):
        if producers:
            producers.refresh()
        items = inbox.latest(limit)
        return {"unread": sum(1 for i in items if not i.read_at), "items": [i.public() for i in items]}

    @api.post("/inbox")
    def ui_add(body: dict = Body(...)):
        unknown = set(body) - {"title", "body", "kind", "ref"}
        if unknown:
            raise HTTPException(422, f"unknown field(s): {', '.join(sorted(unknown))}")
        try:
            item = inbox.add(NewItem(title=body.get("title") or "", body=body.get("body") or "",
                                     kind=body.get("kind") or "note", ref=body.get("ref")))
        except InvalidItem as e:
            raise HTTPException(422, str(e)) from None
        return item.public()

    @api.post("/inbox/{item_id}/read")
    def ui_read(item_id: str, body: dict = Body(default={})):
        item = inbox.mark_read(item_id, read=bool(body.get("read", True)))
        if not item:
            raise HTTPException(404, "no such inbox item")
        return item.public()
