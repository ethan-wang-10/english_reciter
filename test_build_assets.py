import hashlib
import json
import os
from pathlib import Path
import stat
from urllib.parse import urlsplit

import pytest

from scripts import build_assets


@pytest.fixture
def asset_root(tmp_path):
    sources = tmp_path / "static" / "js" / "app"
    sources.mkdir(parents=True)
    for name in build_assets.APP_PARTS:
        (sources / (name + ".js")).write_text("function " + name.replace("-", "_") + "() {}\n", encoding="utf-8")
    css = tmp_path / "static" / "css"
    css.mkdir()
    (css / "style.css").write_text("body { color: black; }\n", encoding="utf-8")
    (tmp_path / "static" / "index.html").write_text(
        '<link rel="stylesheet" href="/static/css/style.css?theme=light&amp;v=old#sheet">\n'
        '<script src="/static/js/app.js?v=manual" defer></script>\n'
        '<script src="https://example.test/remote.js?v=external"></script>\n',
        encoding="utf-8",
    )
    return tmp_path


def manifest(root):
    return json.loads((root / "static" / "asset-manifest.json").read_text(encoding="utf-8"))["assets"]


def test_build_creates_immutable_files_and_is_idempotent(asset_root):
    assert build_assets.build(asset_root)
    assets = manifest(asset_root)
    html = (asset_root / "static" / "index.html").read_text(encoding="utf-8")
    for original, entry in assets.items():
        content = (asset_root / urlsplit(entry["url"]).path.lstrip("/")).read_bytes()
        assert hashlib.sha256(content).hexdigest() == entry["sha256"]
        assert entry["sha256"][:16] in entry["url"]
        assert entry["bytes"] == len(content)
        assert original not in html
    assert "?theme=light#sheet" in assets["/static/css/style.css"]["url"]
    assert "https://example.test/remote.js?v=external" in html
    assert not build_assets.build(asset_root)
    assert not build_assets.build(asset_root, check=True)


def test_source_change_only_invalidates_application_and_keeps_old_urls(asset_root):
    build_assets.build(asset_root)
    before = manifest(asset_root)
    source = asset_root / "static" / "js" / "app" / "session.js"
    source.write_text("function revisedSession() {}\n", encoding="utf-8")
    old_html = (asset_root / "static" / "index.html").read_bytes()
    assert build_assets.build(asset_root, check=True)
    assert (asset_root / "static" / "index.html").read_bytes() == old_html
    build_assets.build(asset_root)
    after = manifest(asset_root)
    assert before["/static/js/app.js"]["url"] != after["/static/js/app.js"]["url"]
    assert before["/static/css/style.css"] == after["/static/css/style.css"]
    assert (asset_root / urlsplit(before["/static/js/app.js"]["url"]).path.lstrip("/")).exists()
    assert "revisedSession" in (asset_root / "static" / "js" / "app.js").read_text(encoding="utf-8")


def test_css_change_updates_its_own_content_address(asset_root):
    build_assets.build(asset_root)
    before = manifest(asset_root)
    (asset_root / "static" / "css" / "style.css").write_text("body { color: green; }\n", encoding="utf-8")
    build_assets.build(asset_root)
    after = manifest(asset_root)
    assert before["/static/css/style.css"]["url"] != after["/static/css/style.css"]["url"]
    assert before["/static/js/app.js"] == after["/static/js/app.js"]


def test_text_asset_hashes_are_independent_of_checkout_line_endings(asset_root):
    css = asset_root / "static" / "css" / "style.css"
    css.write_bytes(b"body { color: black; }\r\n")
    build_assets.build(asset_root)
    before = manifest(asset_root)
    css.write_bytes(b"body { color: black; }\n")
    assert not build_assets.build(asset_root, check=True)
    assert before == manifest(asset_root)


@pytest.mark.parametrize("url", ["/static/js/missing.js", "/static/../../outside.js"])
def test_invalid_asset_fails_before_writing_any_outputs(asset_root, url):
    html = asset_root / "static" / "index.html"
    html.write_text('<script src="' + url + '"></script>', encoding="utf-8")
    with pytest.raises((FileNotFoundError, ValueError)):
        build_assets.build(asset_root)
    assert not (asset_root / "static" / "js" / "app.js").exists()
    assert not (asset_root / "static" / "asset-manifest.json").exists()
    assert not (asset_root / "static" / "assets").exists()


def test_repository_generated_assets_are_current():
    assert not build_assets.build(Path(__file__).resolve().parent, check=True)


@pytest.mark.skipif(os.name != 'posix', reason='POSIX proxy-user permissions')
def test_assets_are_proxy_readable_under_private_deployment_umask(asset_root):
    previous = os.umask(0o077)
    try:
        outputs = build_assets.build(asset_root)
    finally:
        os.umask(previous)
    for path in outputs:
        assert stat.S_IMODE(path.stat().st_mode) == 0o644
    for path in (asset_root / 'static' / 'assets').rglob('*'):
        if path.is_dir():
            assert stat.S_IMODE(path.stat().st_mode) == 0o755
    assert stat.S_IMODE((asset_root / 'static' / 'assets').stat().st_mode) == 0o755
