import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from typer.testing import CliRunner

from orbit_notes import appupdate
from orbit_notes import config as config_mod
from orbit_notes.api import create_app
from orbit_notes.cli import app as cli_app
from orbit_notes.store import Store
from orbit_notes.ui import mount_ui

TOKEN = "test-token"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    for k in ("ORBIT_TOKEN", "ORBIT_DATA_DIR", "ORBIT_VAULT_DIR", "ORBIT_LLM_PROVIDER", "ORBIT_NAME"):
        monkeypatch.delenv(k, raising=False)
    path = tmp_path / "config.toml"
    path.write_text(f'name = "home-pc"\ntoken = "{TOKEN}"\ndata_dir = "data"\ntalk_workspace = "none"\n')
    return config_mod.load(path)


@pytest.fixture
def client(cfg):
    store = Store(cfg.data_dir)
    app = create_app(cfg, store)
    mount_ui(app, cfg, store, None)
    return TestClient(app)


def build_apk(dir_, code=42, name="0.1.0-abc1234", body=b"payload", app_id="com.orbit"):
    """A fake AGP output: app-release.apk (zip magic) + output-metadata.json."""
    dir_.mkdir(parents=True, exist_ok=True)
    apk = dir_ / "app-release.apk"
    apk.write_bytes(b"PK\x03\x04" + body)
    (dir_ / "output-metadata.json").write_text(json.dumps({
        "version": 3, "applicationId": app_id, "variantName": "release",
        "elements": [{"type": "SINGLE", "versionCode": code, "versionName": name, "outputFile": "app-release.apk"}],
    }))
    return apk


def test_latest_404_until_published(client):
    assert client.get("/v1/app/latest", headers=AUTH).status_code == 404
    assert client.get("/v1/app/apk", headers=AUTH).status_code == 404


@pytest.mark.parametrize("path", ["/v1/app/latest", "/v1/app/apk"])
def test_app_routes_need_token(client, cfg, tmp_path, path):
    appupdate.publish(cfg.data_dir, build_apk(tmp_path / "out"))
    assert client.get(path).status_code == 401
    assert client.get(path, headers={"Authorization": "Bearer wrong"}).status_code == 401


def test_publish_reads_build_metadata_and_serves_it(client, cfg, tmp_path):
    apk = build_apk(tmp_path / "out", code=42, name="0.1.0-abc1234")
    rel = appupdate.publish(cfg.data_dir, apk)
    data = apk.read_bytes()
    assert (rel.version_code, rel.version_name, rel.size) == (42, "0.1.0-abc1234", len(data))
    assert rel.sha256 == hashlib.sha256(data).hexdigest()

    r = client.get("/v1/app/latest", headers=AUTH)
    assert r.status_code == 200
    body = r.json()
    assert body["version_code"] == 42 and body["version_name"] == "0.1.0-abc1234"
    assert body["sha256"] == rel.sha256 and body["size"] == len(data) and body["apk"] == "/v1/app/apk"
    assert body["published_at"] and "file" not in body

    r = client.get("/v1/app/apk", headers=AUTH)
    assert r.status_code == 200
    assert r.content == data
    assert r.headers["content-type"] == appupdate.APK_TYPE
    assert r.headers["x-orbit-sha256"] == rel.sha256


def test_publish_refuses_same_or_lower_version_unless_forced(cfg, tmp_path):
    appupdate.publish(cfg.data_dir, build_apk(tmp_path / "a", code=42))
    with pytest.raises(appupdate.PublishError, match="not higher"):
        appupdate.publish(cfg.data_dir, build_apk(tmp_path / "b", code=42, body=b"other"))
    with pytest.raises(appupdate.PublishError, match="not higher"):
        appupdate.publish(cfg.data_dir, build_apk(tmp_path / "c", code=41))
    rel = appupdate.publish(cfg.data_dir, build_apk(tmp_path / "d", code=41, body=b"rollback"), force=True)
    assert appupdate.latest(cfg.data_dir) == rel


def test_publish_keeps_current_and_previous_apk_only(cfg, tmp_path):
    for code in (40, 41, 42):
        appupdate.publish(cfg.data_dir, build_apk(tmp_path / str(code), code=code, body=str(code).encode()))
    names = sorted(p.name for p in appupdate.app_dir(cfg.data_dir).glob("*.apk"))
    assert names == ["orbit-41.apk", "orbit-42.apk"]
    assert not list(appupdate.app_dir(cfg.data_dir).glob(".*.tmp"))


def test_publish_explicit_versions_without_metadata(cfg, tmp_path):
    apk = tmp_path / "x.apk"
    apk.write_bytes(b"PK\x03\x04zz")
    with pytest.raises(appupdate.PublishError, match="output-metadata.json"):
        appupdate.publish(cfg.data_dir, apk)
    rel = appupdate.publish(cfg.data_dir, apk, version_code=7, version_name="0.1.7")
    assert (rel.version_code, rel.version_name) == (7, "0.1.7")


def test_publish_rejects_non_apk_and_other_app(cfg, tmp_path):
    txt = tmp_path / "notes.txt"
    txt.write_text("hello")
    with pytest.raises(appupdate.PublishError, match="not an APK"):
        appupdate.publish(cfg.data_dir, txt, version_code=5, version_name="5")
    with pytest.raises(appupdate.PublishError, match="not com.orbit"):
        appupdate.publish(cfg.data_dir, build_apk(tmp_path / "other", app_id="com.example"))
    assert appupdate.latest(cfg.data_dir) is None


def test_missing_apk_file_means_nothing_published(client, cfg, tmp_path):
    rel = appupdate.publish(cfg.data_dir, build_apk(tmp_path / "out"))
    (appupdate.app_dir(cfg.data_dir) / rel.file).unlink()
    assert client.get("/v1/app/latest", headers=AUTH).status_code == 404


def test_ui_status_shows_published_version(client, cfg, tmp_path):
    assert client.get("/ui/api/status").json()["app"] is None
    appupdate.publish(cfg.data_dir, build_apk(tmp_path / "out", code=43, name="0.1.0-def"))
    app = client.get("/ui/api/status").json()["app"]
    assert app["version_code"] == 43 and app["version_name"] == "0.1.0-def"


def test_cli_publish_apk(cfg, tmp_path):
    apk = build_apk(tmp_path / "out", code=44, name="0.1.0-aaa")
    runner = CliRunner()
    r = runner.invoke(cli_app, ["publish-apk", str(apk), "--config", str(cfg.path)])
    assert r.exit_code == 0, r.output
    assert "0.1.0-aaa" in r.output and appupdate.latest(cfg.data_dir).version_code == 44
    r = runner.invoke(cli_app, ["publish-apk", str(apk), "--config", str(cfg.path)])
    assert r.exit_code == 1 and "not higher" in r.output
