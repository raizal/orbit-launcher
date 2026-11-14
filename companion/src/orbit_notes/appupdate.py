"""Over-the-air Orbit app updates (docs/companion.md -> App updates).

`orbit-notes publish-apk <apk>` copies a release APK and its version metadata into
`<data_dir>/app/`; the watch reads `GET /v1/app/latest` at cheap moments and downloads
`GET /v1/app/apk` when the versionCode is higher than its own. The watch verifies the
sha256 and installs through Android's normal confirmation; nothing here installs anything.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse

APPLICATION_ID = "com.orbit"
APK_TYPE = "application/vnd.android.package-archive"
KEEP_APKS = 2  # the published one + the previous (a watch may be mid-download when a new one lands)


class PublishError(Exception):
    pass


@dataclass(frozen=True)
class AppRelease:
    version_code: int
    version_name: str
    sha256: str
    size: int
    published_at: str
    file: str  # name inside <data_dir>/app/

    def public(self) -> dict:
        """What the watch / web UI see (no file names)."""
        d = asdict(self)
        del d["file"]
        return {**d, "apk": "/v1/app/apk"}


def app_dir(data_dir: Path) -> Path:
    return data_dir / "app"


def latest(data_dir: Path) -> AppRelease | None:
    path = app_dir(data_dir) / "latest.json"
    try:
        raw = json.loads(path.read_text())
        rel = AppRelease(**{k: raw[k] for k in AppRelease.__dataclass_fields__})
    except (OSError, ValueError, KeyError, TypeError):
        return None
    return rel if (app_dir(data_dir) / rel.file).is_file() else None


def build_metadata(apk: Path) -> tuple[int, str]:
    """versionCode/versionName from the `output-metadata.json` AGP writes next to the APK."""
    meta = apk.parent / "output-metadata.json"
    try:
        raw = json.loads(meta.read_text())
    except (OSError, ValueError) as e:
        raise PublishError(f"no readable {meta.name} next to the APK ({e}); pass --version-code and --version-name") from None
    if raw.get("applicationId") not in (None, APPLICATION_ID):
        raise PublishError(f"{meta.name} is for {raw.get('applicationId')!r}, not {APPLICATION_ID}")
    for el in raw.get("elements", []):
        if el.get("outputFile") == apk.name:
            return int(el["versionCode"]), str(el.get("versionName") or el["versionCode"])
    raise PublishError(f"{apk.name} is not listed in {meta.name}; pass --version-code and --version-name")


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def publish(data_dir: Path, apk: Path, version_code: int | None = None, version_name: str | None = None,
            force: bool = False, now: Callable[[], datetime] = lambda: datetime.now().astimezone()) -> AppRelease:
    """Copy [apk] in and point latest.json at it. Atomic: the watch sees the old or the new release, never half."""
    if not apk.is_file():
        raise PublishError(f"not a file: {apk}")
    with apk.open("rb") as f:
        if f.read(4) != b"PK\x03\x04":
            raise PublishError(f"not an APK (zip) file: {apk}")
    if version_code is None or version_name is None:
        code, name = build_metadata(apk)
        version_code = code if version_code is None else version_code
        version_name = name if version_name is None else version_name
    if version_code < 1:
        raise PublishError("versionCode must be >= 1")
    current = latest(data_dir)
    if current and version_code <= current.version_code and not force:
        raise PublishError(f"versionCode {version_code} is not higher than the published {current.version_code} "
                           f"({current.version_name}); the watch would ignore it. Use --force to replace anyway.")

    d = app_dir(data_dir)
    d.mkdir(parents=True, exist_ok=True)
    name = f"orbit-{version_code}.apk"
    tmp = d / f".{name}.tmp"
    shutil.copyfile(apk, tmp)
    digest = sha256_of(tmp)  # hash the copy we serve, not the source (it could change underneath)
    rel = AppRelease(version_code, version_name, digest, tmp.stat().st_size, now().isoformat(timespec="seconds"), name)
    os.replace(tmp, d / name)
    tmp_json = d / ".latest.json.tmp"
    tmp_json.write_text(json.dumps(asdict(rel), indent=2) + "\n")
    os.replace(tmp_json, d / "latest.json")
    _prune(d, keep={name, current.file if current else name})
    return rel


def _prune(d: Path, keep: set[str]) -> None:
    for apk in d.glob("orbit-*.apk"):
        if apk.name not in keep:
            apk.unlink(missing_ok=True)


def router(data_dir: Path, require_token: Callable[..., None]) -> APIRouter:
    r = APIRouter(prefix="/v1/app", dependencies=[Depends(require_token)])

    @r.get("/latest")
    def get_latest():
        rel = latest(data_dir) or _none()
        return rel.public()

    @r.get("/apk")
    def get_apk():
        rel = latest(data_dir) or _none()
        return FileResponse(app_dir(data_dir) / rel.file, media_type=APK_TYPE, filename=f"orbit-{rel.version_name}.apk",
                            headers={"X-Orbit-Sha256": rel.sha256, "X-Orbit-Version-Code": str(rel.version_code)})

    return r


def _none():
    raise HTTPException(404, "no app build published")
