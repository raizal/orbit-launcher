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
    from .talk import Talker
    from .transcribe import Transcriber, Worker
    from .ui import mount_ui

    cfg, store = _load(config)
    talker = Talker(cfg, store) if cfg.talk_provider != "none" else None
    w = Worker(store, Transcriber(cfg), talker) if worker else None
    app_ = create_app(cfg, store, on_stored=w.enqueue if w else (lambda _id: None),
                      lifespan=serve_lifespan(cfg, w, advertise=mdns))
    mount_ui(app_, cfg, store, w.enqueue if w else None)
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


def main() -> None:
    app()
