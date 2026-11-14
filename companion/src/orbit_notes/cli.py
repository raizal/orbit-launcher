"""`orbit-notes` command line."""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path
from typing import Annotated, Optional

import typer

from . import config as config_mod
from .config import Config
from .store import Store

app = typer.Typer(no_args_is_help=True, add_completion=False, help="Orbit Notes companion: receive, transcribe and diarise watch voice notes.")

ConfigOpt = Annotated[
    Optional[Path],
    typer.Option("--config", "-c", help="config.toml (default: ./config.toml, else ~/.config/orbit-notes/config.toml)"),
]


def _load(path: Path | None) -> tuple[Config, Store]:
    cfg = config_mod.load(path)
    return cfg, Store(cfg.data_dir)


@app.command()
def serve(config: ConfigOpt = None, mdns: bool = typer.Option(True, help="Advertise _orbitnotes._tcp"),
          worker: bool = typer.Option(True, help="Run the background transcription worker")):
    """HTTP API + mDNS advertise + background transcription worker."""
    import uvicorn

    from .api import create_app, serve_lifespan
    from .memory import Memory
    from .talk import Talker
    from .transcribe import Transcriber, Worker
    from .ui import mount_ui

    cfg, store = _load(config)
    memory = Memory(cfg, store)  # the embedding model loads lazily (first search / background indexing)
    talker = Talker(cfg, store, mem=memory) if cfg.talk_provider != "none" else None
    w = Worker(store, Transcriber(cfg), talker, memory) if worker else None
    app_ = create_app(cfg, store, on_stored=w.enqueue if w else (lambda _id: None),
                      lifespan=serve_lifespan(cfg, w, advertise=mdns))
    mount_ui(app_, cfg, store, w.enqueue if w else None, tts=talker.tts if talker else None, memory=memory)
    typer.echo(f"Orbit Notes '{cfg.name}' on {cfg.host}:{cfg.port}  config={cfg.path}  data={cfg.data_dir}")
    typer.echo(f"UI (this computer only): http://localhost:{cfg.port}/ui/")
    uvicorn.run(app_, host=cfg.host, port=cfg.port)


@app.command()
def pair(config: ConfigOpt = None,
         name: Annotated[Optional[str], typer.Option(help="Server name shown on the watch (saved to config)")] = None):
    """Print the pairing QR code in the terminal and save it as PNG."""
    import qrcode

    cfg, _ = _load(config)
    if name and name != cfg.name:
        config_mod.set_key(cfg.path, "name", name)
        cfg.name = name
    payload = json.dumps({"name": cfg.name, "port": cfg.port, "token": cfg.token}, separators=(",", ":"))
    qr = qrcode.QRCode(border=2, error_correction=qrcode.constants.ERROR_CORRECT_M)
    qr.add_data(payload)
    qr.print_ascii(invert=True)
    png = cfg.data_dir / "pairing-qr.png"
    qr.make_image().save(png)
    png.chmod(0o600)
    typer.echo(f"Scan with Orbit on the watch. Server name: {cfg.name}, port {cfg.port}.")
    typer.echo(f"PNG saved to {png} (contains the token; keep it private).")


@app.command()
def transcribe(config: ConfigOpt = None, all_: bool = typer.Option(False, "--all", help="Re-transcribe every note")):
    """Transcribe notes that have no transcript yet."""
    from .transcribe import Transcriber

    cfg, store = _load(config)
    notes = store.pending_transcripts(include_done=all_)
    if not notes:
        typer.echo("Nothing to transcribe.")
        return
    t = Transcriber(cfg)
    ok = 0
    for n in notes:
        typer.echo(n.path)
        ok += t.run(store, n)
    typer.echo(f"Transcribed {ok}/{len(notes)}.")
    raise typer.Exit(0 if ok == len(notes) else 1)


@app.command()
def diary(day: Annotated[Optional[str], typer.Argument(help="Local date, YYYY-MM-DD")] = None,
          yesterday: bool = typer.Option(False, "--yesterday", help="Build yesterday's page (for cron)"),
          config: ConfigOpt = None):
    """Build (or rebuild) the diary page for one day."""
    from . import diary as diary_mod

    if yesterday == bool(day):
        raise typer.BadParameter("give a date or --yesterday (not both)")
    d = date.today() - timedelta(days=1) if yesterday else date.fromisoformat(day)
    cfg, store = _load(config)
    page = diary_mod.build(cfg, store, d)
    typer.echo(f"Wrote {page}")


memory_app = typer.Typer(no_args_is_help=True, help="Memory & recall: episodic index and facts (docs/memory.md).")
app.add_typer(memory_app, name="memory")


@memory_app.command("backfill")
def memory_backfill(config: ConfigOpt = None,
                    threads: int = typer.Option(4, help="CPU threads for the embedding model")):
    """Index the whole archive (text + vectors). Incremental: unchanged notes are skipped."""
    import time

    from .memory import E5Embedder, Memory

    cfg, store = _load(config)
    mem = Memory(cfg, store)
    if isinstance(mem.embedder, E5Embedder):
        mem.embedder.threads = threads
    t = time.monotonic()
    n = mem.sync(progress=typer.echo)
    typer.echo(f"text: {n} source(s) (re)indexed in {time.monotonic() - t:.1f}s")
    t2 = time.monotonic()
    if mem.embedder:
        mem.embedder.load()
        t3 = time.monotonic()
        e = mem.embed_pending(batch=64)
        typer.echo(f"vectors: model load {t3 - t2:.1f}s, {e} chunk(s) embedded in {time.monotonic() - t3:.1f}s")
    typer.echo(json.dumps(mem.stats(), indent=1))


@memory_app.command("search")
def memory_search(query: str, config: ConfigOpt = None, limit: int = 8,
                  date_from: Annotated[Optional[str], typer.Option("--from")] = None,
                  date_to: Annotated[Optional[str], typer.Option("--to")] = None):
    """Hybrid search (keyword + meaning) over the episodic index."""
    from .memory import Memory

    cfg, store = _load(config)
    mem = Memory(cfg, store)
    a = date.fromisoformat(date_from) if date_from else None
    b = date.fromisoformat(date_to) if date_to else None
    for h in mem.search(query, limit=limit, date_from=a, date_to=b):
        sim = f"{h.sim:.3f}" if h.sim is not None else "  -  "
        typer.echo(f"{h.score:.4f} sim {sim}{' kw' if h.keyword else '   '}  {h.when()}  {h.label()}: {h.text[:160]}")
inbox_app = typer.Typer(no_args_is_help=True, help="Orbit Inbox: send short items to the watch (docs/inbox.md).")
app.add_typer(inbox_app, name="inbox")


@inbox_app.command("add")
def inbox_add(title: Annotated[str, typer.Option(help="Short, spoken-friendly title")],
              body: Annotated[str, typer.Option(help="One or two sentences")] = "",
              kind: Annotated[str, typer.Option(help="note, thread_done, app_update, ...")] = "note",
              ref: Annotated[Optional[str], typer.Option(help="Reference, e.g. a BB thread id")] = None,
              config: ConfigOpt = None):
    """Add an item; the watch picks it up on its next check (works with or without `serve` running)."""
    from .inbox import Inbox, InvalidItem, NewItem

    cfg = config_mod.load(config)
    try:
        item = Inbox(cfg.data_dir).add(NewItem(title=title, body=body, kind=kind, ref=ref))
    except InvalidItem as e:
        raise typer.BadParameter(str(e)) from None
    typer.echo(json.dumps(item.public()))


@inbox_app.command("list")
def inbox_list(limit: int = typer.Option(20, help="How many, newest first"), config: ConfigOpt = None):
    """Newest items with their read state."""
    from .inbox import Inbox

    cfg = config_mod.load(config)
    for i in Inbox(cfg.data_dir).latest(limit):
        typer.echo(f"{i.created_at[:16]}  {'  ' if i.read_at else '● '}{i.id}  [{i.kind}] {i.title}"
                   + (f" — {i.body[:80]}" if i.body else ""))


@inbox_app.command("read")
def inbox_read(item_id: str, unread: bool = typer.Option(False, "--unread", help="Mark unread instead"),
               config: ConfigOpt = None):
    """Mark an item read (or unread)."""
    from .inbox import Inbox

    cfg = config_mod.load(config)
    item = Inbox(cfg.data_dir).mark_read(item_id, read=not unread)
    if not item:
        raise typer.BadParameter(f"no inbox item {item_id}")
    typer.echo(json.dumps(item.public()))


@app.command("publish-apk")
def publish_apk(apk: Annotated[Path, typer.Argument(help="Release APK, e.g. app/build/outputs/apk/release/app-release.apk")],
                config: ConfigOpt = None,
                version_code: Annotated[Optional[int], typer.Option(help="Default: from output-metadata.json next to the APK")] = None,
                version_name: Annotated[Optional[str], typer.Option(help="Default: from output-metadata.json next to the APK")] = None,
                force: bool = typer.Option(False, "--force", help="Replace even if the versionCode is not higher")):
    """Publish an Orbit build for the watch to update itself over Wi-Fi (docs/companion.md -> App updates)."""
    from . import appupdate

    cfg = config_mod.load(config)
    try:
        rel = appupdate.publish(cfg.data_dir, apk.expanduser(), version_code, version_name, force=force)
    except appupdate.PublishError as e:
        typer.echo(f"Not published: {e}", err=True)
        raise typer.Exit(1) from None
    typer.echo(f"Published Orbit {rel.version_name} (versionCode {rel.version_code}, {rel.size} bytes, sha256 {rel.sha256[:12]}…)")
    typer.echo(f"→ {appupdate.app_dir(cfg.data_dir) / rel.file}. The watch offers it at its next check (home Wi-Fi connect, "
               "after uploads, or every 6 h; Servers screen → refresh checks now).")


def main() -> None:
    app()
