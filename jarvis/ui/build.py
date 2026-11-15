"""Build jarvis/ui/web/orb.html: inline the thinking-orbs engine (MIT) into the template as a classic script.

The engine ships as an ES module (`export { ... }`); Chromium refuses module imports from file:// so we rewrite the
single export statement into a `window.ThinkingOrbsEngine = {...}` assignment. Nothing else is modified.
Source: third_party/thinking-orbs/package/dist/engine.es.js (v0.3.1, © 2026 Jakub Antalik, MIT).
"""
from __future__ import annotations

import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
ENGINE = ROOT / "third_party" / "thinking-orbs" / "package" / "dist" / "engine.es.js"
TEMPLATE = HERE / "web" / "orb.template.html"
OUT = HERE / "web" / "orb.html"

_EXPORT = re.compile(r"export\s*\{([^}]*)\}\s*;?\s*$", re.S)


def engine_as_classic_script() -> str:
    src = ENGINE.read_text(encoding="utf-8")
    m = _EXPORT.search(src)
    if not m:
        raise RuntimeError("thinking-orbs engine: export block not found (API changed?)")
    pairs = []
    for part in m.group(1).split(","):
        part = part.strip()
        if not part:
            continue
        if " as " in part:
            local, public = (x.strip() for x in part.split(" as "))
        else:
            local = public = part
        pairs.append(f"{public}: {local}")
    body = src[: m.start()]
    return "(function(){\n" + body + "\nwindow.ThinkingOrbsEngine = {" + ", ".join(pairs) + "};\n})();"


def build() -> Path:
    html = TEMPLATE.read_text(encoding="utf-8")
    if "/*__THINKING_ORBS_ENGINE__*/" not in html:
        raise RuntimeError("template marker missing")
    out = html.replace("/*__THINKING_ORBS_ENGINE__*/", engine_as_classic_script())
    OUT.write_text(out, encoding="utf-8")
    return OUT


if __name__ == "__main__":
    print(build())
