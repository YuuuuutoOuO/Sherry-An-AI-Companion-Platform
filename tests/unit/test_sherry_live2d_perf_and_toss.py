"""
Unit tests verifying the Live2D performance optimizations and toss interaction mechanics.
"""

import re
from pathlib import Path

import pytest


@pytest.fixture
def repo_root():
    return Path(__file__).resolve().parent.parent.parent


def test_live2d_js_syntax_and_balancing(repo_root):
    js_path = repo_root / "web_ui" / "static" / "sherry_live2d.js"
    assert js_path.exists(), "sherry_live2d.js must exist"
    content = js_path.read_text(encoding="utf-8")

    # Basic structural balance verification (brackets and braces outside strings/comments)
    stack = []
    pairs = {')': '(', ']': '[', '}': '{'}
    in_string = None
    in_line_comment = False
    in_block_comment = False
    escape = False

    i = 0
    n = len(content)
    line = 1
    col = 0

    while i < n:
        c = content[i]
        col += 1
        if c == '\n':
            line += 1
            col = 0
            in_line_comment = False
            i += 1
            continue

        if in_line_comment:
            i += 1
            continue

        if in_block_comment:
            if c == '*' and i + 1 < n and content[i + 1] == '/':
                in_block_comment = False
                i += 2
                col += 1
                continue
            i += 1
            continue

        if in_string:
            if escape:
                escape = False
            elif c == '\\':
                escape = True
            elif c == in_string:
                in_string = None
            i += 1
            continue

        # Check start of comments
        if c == '/' and i + 1 < n:
            next_c = content[i + 1]
            if next_c == '/':
                in_line_comment = True
                i += 2
                col += 1
                continue
            elif next_c == '*':
                in_block_comment = True
                i += 2
                col += 1
                continue

        # Check strings
        if c in ("'", '"', '`'):
            in_string = c
            i += 1
            continue

        # Check brackets
        if c in ('(', '[', '{'):
            stack.append((c, line, col))
        elif c in (')', ']', '}'):
            assert stack, f"Unmatched closing '{c}' at {line}:{col}"
            top, tl, tc = stack.pop()
            assert top == pairs[c], f"Mismatched '{c}' at {line}:{col}, expected matching for '{top}' at {tl}:{tc}"

        i += 1

    assert not stack, f"Unclosed brackets remain: {stack[-3:]}"


def test_live2d_js_optimizations(repo_root):
    js_path = repo_root / "web_ui" / "static" / "sherry_live2d.js"
    content = js_path.read_text(encoding="utf-8")

    # 1. Remix downsampling
    assert "targetDim" in content, "Remix canvas downsample dimension must be defined"
    assert "this.canvas.width = targetDim;" in content
    assert "this.canvas.height = targetDim;" in content

    # 2. Scale transform in renderRemixModel
    assert "const scale = this.canvas.width / 900;" in content
    assert "this.ctx.scale(scale, scale);" in content
    assert "this.ctx.restore(); // End Remix Scale Group" in content

    # 3. Hitbox coordinate scaling
    assert "const normScale = 900 / this.canvas.width;" in content
    assert "x = x * normScale;" in content
    assert "y = y * normScale;" in content

    # 4. Geometry caching: getBoundingClientRect must NOT be in the mousemove look loop
    # Extract window.addEventListener('mousemove', ...)
    mousemove_match = re.search(r"window\.addEventListener\('mousemove',\s*\([^\)]*\)\s*=>\s*\{(.*?)\n\s*\}\);", content, re.DOTALL)
    assert mousemove_match is not None, "window.mousemove listener must exist"
    mousemove_body = mousemove_match.group(1)
    assert "this.canvas.getBoundingClientRect()" not in mousemove_body, "getBoundingClientRect must not be called inside mousemove handler"
    assert "this.cachedCanvasRect" in mousemove_body, "cachedCanvasRect must be utilized inside mousemove"

    # 5. Toss interaction in mouseup
    mouseup_match = re.search(r"window\.addEventListener\('mouseup',\s*\([^\)]*\)\s*=>\s*\{(.*?)\n\s*\}\);", content, re.DOTALL)
    assert mouseup_match is not None, "window.mouseup listener must exist"
    mouseup_body = mouseup_match.group(1)
    assert "this.tossHistory.push({ x: e.clientX, y: e.clientY, time: now });" in mouseup_body, "mouseup must sample final release position"
    assert "targetFps" in mouseup_body, "toss velocity must be adapted to physics fpsInterval"
    assert "<= 220" in mouseup_body, "toss sampling window must be relaxed to 220ms"
    assert "isRecent" in mouseup_body, "isRecent check must prevent false flings when holding stationary before release"

    # 6. Race condition fix in loadModelTextures
    assert "img.complete" in content and "img.naturalWidth !== 0" in content, "img.complete cache hit fallback must be implemented"
    assert "finish(img.naturalWidth !== 0)" in content, "Image finish callback must safely resolve both load and error paths"

    # 7. Low-friction screen bouncing physics & visual movement margin expansion
    assert "getMovementBounds()" in content, "getMovementBounds helper method must be declared"
    assert "this.gravity = 0.45;" in content, "gravity must be softened to allow airy toss flight"
    assert "this.airFriction = 0.996;" in content, "air friction must be near zero for long screen travel"
    assert "this.wallBounce = 0.88;" in content, "wall elasticity must be high for screen bouncing"
    assert "this.floorBounce = 0.80;" in content, "floor bounce must be elastic"
    assert "this.floorFriction = 0.97;" in content, "floor friction must be low for sliding"


def test_live2d_css_optimizations(repo_root):
    css_path = repo_root / "web_ui" / "static" / "sherry_live2d.css"
    assert css_path.exists(), "sherry_live2d.css must exist"
    content = css_path.read_text(encoding="utf-8")

    # will-change: transform
    assert "will-change: transform;" in content, "container must declare will-change: transform"

    # Filter disabling in tossing / dragging states
    assert ".sherry-live2d-container.is-tossing" in content
    assert ".sherry-live2d-container.is-dragging" in content
    assert "filter: none;" in content, "filter must be disabled or lightened during dragging/tossing"

    # Remix Chibi 1:1 container height adaptation (eliminate 160px top deadzone)
    assert ".sherry-live2d-container.model-remix" in content
    assert "height: 240px;" in content


def test_pwa_and_template_cache_busting_sync(repo_root):
    index_path = repo_root / "web_ui" / "templates" / "index.html"
    sw_path = repo_root / "web_ui" / "static" / "sw.js"
    assert index_path.exists() and sw_path.exists()

    index_html = index_path.read_text(encoding="utf-8")
    sw_js = sw_path.read_text(encoding="utf-8")

    # Extract versions from index.html
    css_v_match = re.search(r"/static/sherry_live2d\.css\?v=(\d+)", index_html)
    js_v_match = re.search(r"/static/sherry_live2d\.js\?v=(\d+)", index_html)
    assert css_v_match is not None, "Live2D CSS version query must exist in index.html"
    assert js_v_match is not None, "Live2D JS version query must exist in index.html"

    # Verify matching versions in sw.js APP_SHELL
    assert f"/static/sherry_live2d.css?v={css_v_match.group(1)}" in sw_js, "sw.js must cache the exact same CSS version as index.html"
    assert f"/static/sherry_live2d.js?v={js_v_match.group(1)}" in sw_js, "sw.js must cache the exact same JS version as index.html"

