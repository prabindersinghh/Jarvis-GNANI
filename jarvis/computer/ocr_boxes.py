"""OCR with line/word bounding boxes via Windows.Media.Ocr (JARVIS extension of engine.ocr).

engine.ocr returns plain text only. The OCR routing path (MASTER §9) needs
*structured candidates* — text plus a clickable location — so this module runs
the same WinRT engine but emits JSON lines:

    {"lines": [{"text": "Save As", "bounds": [l, t, r, b],
                "words": [{"text": "Save", "bounds": [...]}, ...]}]}

Bounds are in the coordinate space of the image handed in; callers add the
capture origin to map back to physical screen pixels.
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import tempfile
import time

from .engine import ocr as _ocr

log = logging.getLogger("jarvis.ocr")

_OCR_JSON_SCRIPT = _ocr._OCR_PS_SCRIPT.replace(
    "[Console]::Out.Write($result.Text)",
    r"""
$lines = @()
foreach ($line in $result.Lines) {
    $words = @()
    $l=[double]::MaxValue; $t=[double]::MaxValue; $r=0; $b=0
    foreach ($w in $line.Words) {
        $rc = $w.BoundingRect
        $words += ,@{ text = $w.Text; bounds = @([int]$rc.X, [int]$rc.Y, [int]($rc.X+$rc.Width), [int]($rc.Y+$rc.Height)) }
        if ($rc.X -lt $l) { $l = $rc.X }
        if ($rc.Y -lt $t) { $t = $rc.Y }
        if (($rc.X+$rc.Width) -gt $r) { $r = $rc.X+$rc.Width }
        if (($rc.Y+$rc.Height) -gt $b) { $b = $rc.Y+$rc.Height }
    }
    $lines += ,@{ text = $line.Text; bounds = @([int]$l,[int]$t,[int]$r,[int]$b); words = $words }
}
[Console]::Out.Write((ConvertTo-Json -Depth 6 -Compress @{ lines = $lines }))
""",
)


def ocr_boxes(img, origin: tuple[int, int] = (0, 0), scale: float = 1.0) -> dict:
    """Run OCR → {"lines": [...], "text": str, "ocr_ms": int}. Bounds mapped to screen space via origin/scale."""
    t0 = time.perf_counter()
    tmp = None
    try:
        fd, tmp = tempfile.mkstemp(suffix=".png", prefix="jarvis_ocr_")
        os.close(fd)
        save = img if img.mode in ("RGB", "RGBA", "L") else img.convert("RGB")
        save.save(tmp, format="PNG")
        proc = _ocr._run_powershell(_OCR_JSON_SCRIPT, image_path=os.path.normpath(os.path.abspath(tmp)),
                                    timeout=_ocr._TIMEOUT_SECONDS)
        if proc.returncode != 0:
            log.warning("OCR exited %s: %s", proc.returncode, proc.stderr.strip()[:300])
            return {"lines": [], "text": "", "ocr_ms": _ms(t0), "error": proc.stderr.strip()[:300]}
        data = json.loads(proc.stdout or "{}")
        lines = data.get("lines") or []
        if isinstance(lines, dict):  # ConvertTo-Json collapses single-item arrays
            lines = [lines]
        ox, oy = origin
        out = []
        for ln in lines:
            words = ln.get("words") or []
            if isinstance(words, dict):
                words = [words]
            out.append({
                "text": ln.get("text", ""),
                "bounds": _map(ln.get("bounds"), ox, oy, scale),
                "words": [{"text": w.get("text", ""), "bounds": _map(w.get("bounds"), ox, oy, scale)} for w in words],
            })
        return {"lines": out, "text": "\n".join(x["text"] for x in out), "ocr_ms": _ms(t0)}
    except subprocess.TimeoutExpired:
        return {"lines": [], "text": "", "ocr_ms": _ms(t0), "error": "ocr timeout"}
    except Exception as e:  # noqa: BLE001
        log.warning("OCR failed: %s", e)
        return {"lines": [], "text": "", "ocr_ms": _ms(t0), "error": str(e)}
    finally:
        if tmp:
            try:
                os.unlink(tmp)
            except OSError:
                pass


def _map(b, ox, oy, scale):
    if not b or len(b) != 4:
        return None
    return [int(ox + b[0] / scale), int(oy + b[1] / scale), int(ox + b[2] / scale), int(oy + b[3] / scale)]


def _ms(t0):
    return round((time.perf_counter() - t0) * 1000)


def ocr_elements(lines: list[dict], start_id: int = 1) -> list[dict]:
    """Turn OCR lines into pseudo-elements compatible with the UIA element model (role='ocr')."""
    els = []
    for i, ln in enumerate(lines):
        if not ln.get("text") or not ln.get("bounds"):
            continue
        els.append({"id": start_id + i, "role": "ocr", "name": ln["text"][:120], "enabled": True,
                    "visible": True, "bounds": ln["bounds"]})
    return els
