"""Run the *built* Vue OBS UI in sandboxed Chromium without localhost access.

Playwright's sandbox blocks page.goto(127.0.0.1), so only browser tests
inline the bundled, production JS/CSS after HTML. Production uses hashed
local /assets/ URLs and never inlines the source or calls external CDNs.
"""
from __future__ import annotations

import re

from app.obs_source import load_frontend_assets

_html, _FRONTEND_ASSETS = load_frontend_assets()
OVERLAY_HTML = _html.decode("utf-8")


def bundle_source(extension: str) -> str:
    matches = [contents.decode('utf8') for name, (contents, _) in _FRONTEND_ASSETS.items()
               if name.endswith(extension) and not name.startswith('/assets/vue3-')]
    if len(matches) != 1:
        raise AssertionError(f'Expected one {extension} overlay bundle; got {len(matches)}')
    return matches[0]


def browser_html(query: str = '') -> str:
    html = OVERLAY_HTML
    for asset, (contents, content_type) in _FRONTEND_ASSETS.items():
        if content_type.startswith('text/css'):
            html = html.replace(f'<link rel="stylesheet" href="{asset}">',
                                '<style>' + contents.decode('utf8') + '</style>')
    scripts = []
    for asset in re.findall(r'<script src="(/assets/[^\"]+)" defer></script>', html):
        scripts.append('<script>' + _FRONTEND_ASSETS[asset][0].decode('utf8') + '</script>')
        html = html.replace(f'<script src="{asset}" defer></script>', '')
    html = html.replace('</body>', ''.join(scripts) + '\n</body>')
    if query:
        html = html.replace('new URLSearchParams(location.search)',
                            'new URLSearchParams(' + repr(query) + ')')
    assert '<script src=' not in html
    return html
