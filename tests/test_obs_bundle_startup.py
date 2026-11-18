"""Regression tests for the missing OBS Vue frontend startup crash."""
from __future__ import annotations

import importlib
import re
import sys
from pathlib import Path

import pytest

from app import obs_source


def test_compiled_bundle_is_complete_and_self_contained() -> None:
    index, assets = obs_source.load_frontend_assets()
    page = index.decode("utf-8")
    assert "Spotify Overlay Lyrics" in page
    assert "__OVERLAY_JS__" not in page
    assert len(assets) == 3
    assert len([name for name in assets if name.endswith(".js")]) == 2
    assert len([name for name in assets if name.endswith(".css")]) == 1
    for ref in re.findall(r'(?:src|href)="(/assets/[^"]+)"', page):
        assert ref in assets
        assert assets[ref][0]
    assert "https://" not in page


def test_missing_bundle_is_optional_at_module_import(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(obs_source, "_WEB_ROOT", tmp_path / "missing")
    # Importing the OBS module must be safe even without a compiled Vue bundle.
    assert obs_source.make_overlay_state(None, obs_source.LyricsResult(kind="missing"))["kind"] == "missing"
    server = obs_source.ObsOverlayServer(port=0)
    with pytest.raises(FileNotFoundError, match="frontend/dist/index.html"):
        server.start()
    assert server._httpd is None
    assert server._thread is None


def test_partial_bundle_is_rejected_instead_of_responding_with_broken_page(tmp_path: Path) -> None:
    page = tmp_path / "index.html"
    page.write_text('<script src="/assets/app.js"></script>'
                    '<script src="/assets/vue.js"></script>'
                    '<link href="/assets/main.css" rel="stylesheet">', encoding="utf-8")
    with pytest.raises(FileNotFoundError, match="缺少編譯資源"):
        obs_source.load_frontend_assets(tmp_path)


def test_build_script_checks_assets_before_packaging() -> None:
    src = Path(__file__).resolve().parents[1]
    launch = (src / "scripts/launcher.ps1").read_text(encoding="utf-8-sig")
    ui = (src / "app/ui.py").read_text(encoding="utf-8")
    assert 'Assert-FrontendReady' in launch
    assert "'--add-data', 'frontend\\dist;frontend\\dist'" in launch
    assert 'except (OSError, RuntimeError) as exc' in ui
