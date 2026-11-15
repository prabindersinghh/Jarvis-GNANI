r"""JARVIS offline latency harness — Performance/UX directive §1, §45, §46, §47.

Measures local, offline-measurable legs with p50/p75/p95, including read-only UIA/OCR/screenshot and adaptive
endpointing. Gnani Prisma/Timbre network calls are excluded; voice API performance belongs in live tests with a key.
It deliberately does NOT drive the mouse, open apps, or play audio, so it is safe to run while the user is working.
The legs that require a live audible/desktop session — the full ORB_CLICK→…→FIRST_AUDIO wall-clock chain and live
barge-in timing — are not measured here; the report notes them and leaves them for a session when the user is away.

    python -m benchmarks.latency.bench --label before            # write before.json / before.md
    python -m benchmarks.latency.bench --label after             # write after.json / after.md
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from jarvis import config                                    # noqa: E402
from jarvis.voice.endpoint import AdaptiveEndpointer, EndpointConfig, Turn, legacy_should_finalize  # noqa: E402

HERE = Path(__file__).resolve().parent
FRAME_S = 0.03  # 30 ms frames, matching the recorder's blocksize


# ------------------------------------------------------------------ measurement utilities
def pct(xs: list[float], p: float) -> float:
    if not xs:
        return 0.0
    xs = sorted(xs)
    k = (len(xs) - 1) * p
    lo, hi = int(k), min(int(k) + 1, len(xs) - 1)
    return round(xs[lo] + (xs[hi] - xs[lo]) * (k - lo), 1)


def summarize(xs: list[float]) -> dict:
    xs = [x for x in xs if x is not None]
    if not xs:
        return {"n": 0}
    return {"n": len(xs), "p50": pct(xs, 0.50), "p75": pct(xs, 0.75), "p95": pct(xs, 0.95),
            "min": round(min(xs), 1), "max": round(max(xs), 1), "mean": round(statistics.fmean(xs), 1)}


def timed(fn, iters: int = 7, warmup: int = 1) -> list[float]:
    for _ in range(warmup):
        try:
            fn()
        except Exception:  # noqa: BLE001
            break
    out = []
    for _ in range(iters):
        t0 = time.perf_counter()
        try:
            fn()
        except Exception as e:  # noqa: BLE001
            print(f"    (iteration failed: {e})")
            continue
        out.append((time.perf_counter() - t0) * 1000)
    return out


# ------------------------------------------------------------------ individual legs
def leg_context(results: dict):
    from jarvis.safety import privacy
    sample = ("Open chrome and go to gmail then search for the invoice from Acme, "
              "my password is hunter2 and card 4111 1111 1111 1111. " * 3)
    results["context_redact"] = summarize(timed(lambda: privacy.redact_text(sample), iters=50, warmup=3))
    try:
        from jarvis.memory import store as memory
        results["context_memory_brief"] = summarize(timed(lambda: memory.brief(sample, "chrome.exe"), iters=20, warmup=2))
    except Exception as e:  # noqa: BLE001
        results["context_memory_brief"] = {"n": 0, "note": str(e)}


def leg_uia_ocr_screen(results: dict):
    # All read-only: observe the CURRENT foreground window, OCR/screenshot a small region. No mouse, no launch.
    try:
        from jarvis.computer import uia
        def _obs():
            _w, els, _m = uia.snapshot(None, max_elements=120)
            return els
        results["uia_observe_foreground"] = summarize(timed(_obs, iters=6, warmup=1))
    except Exception as e:  # noqa: BLE001
        results["uia_observe_foreground"] = {"n": 0, "note": str(e)}
    try:
        from jarvis.computer.engine import capture, winfind
        hwnd = winfind.foreground_window()
        def _shot():
            img, _m, _ok = capture.grab_window_region(hwnd, (0.0, 0.0, 1.0, 0.25))
            return img
        results["screenshot_window_quarter"] = summarize(timed(_shot, iters=6, warmup=1))
    except Exception as e:  # noqa: BLE001
        results["screenshot_window_quarter"] = {"n": 0, "note": str(e)}
    try:
        from jarvis.computer.engine import capture, ocr, winfind
        if ocr.available():
            hwnd = winfind.foreground_window()
            img, _m, _ok = capture.grab_window_region(hwnd, (0.0, 0.0, 1.0, 0.4))
            results["ocr_window_region"] = summarize(timed(lambda: ocr.ocr_image(img), iters=4, warmup=1))
        else:
            results["ocr_window_region"] = {"n": 0, "note": "windows ocr unavailable"}
    except Exception as e:  # noqa: BLE001
        results["ocr_window_region"] = {"n": 0, "note": str(e)}


def leg_claude(results: dict):
    """Real measurement of the streaming-TTS win (§12, §23): with streaming, speech starts at the first speakable
    clause; without it, only after the whole reply. Uses purely conversational prompts (no computer tools) so nothing
    on the desktop is touched. Spends a little Claude quota — opt-in via --claude."""
    import asyncio

    from jarvis.app.claude_session import ClaudeSession
    from jarvis.voice.tts import SpeechChunker

    prompts = ["In one short sentence, say hello and tell me you are ready.",
               "In two short sentences, suggest a simple way to stay focused while working."]

    async def go():
        rows = []
        sess = ClaudeSession()
        t0 = time.perf_counter()
        await sess.connect(resume_previous=False)
        connect_ms = round((time.perf_counter() - t0) * 1000)
        for p in prompts:
            chunker = SpeechChunker("conversational")
            start = time.perf_counter()
            first_clause = {"ms": None}

            def on_text(delta, _c=chunker, _s=start, _fc=first_clause):
                if _fc["ms"] is None and _c.push(delta):
                    _fc["ms"] = round((time.perf_counter() - _s) * 1000)
            sess.on_text = on_text
            r = await sess.ask(p)
            rows.append({"first_token_ms": r.get("first_token_ms"), "first_clause_ms": first_clause["ms"],
                         "claude_ms": r.get("claude_ms"), "num_turns": r.get("num_turns")})
        await sess.disconnect()
        return connect_ms, rows

    try:
        loop = asyncio.new_event_loop()
        connect_ms, rows = loop.run_until_complete(go())
        loop.close()
        clause = [r["first_clause_ms"] for r in rows if r["first_clause_ms"]]
        total = [r["claude_ms"] for r in rows if r["claude_ms"]]
        results["claude_connect_cold"] = {"once_ms": connect_ms, "n": 1}
        results["claude_first_clause"] = summarize(clause)
        results["claude_full_turn"] = summarize(total)
        results["claude_turns"] = rows
    except Exception as e:  # noqa: BLE001
        results["claude"] = {"n": 0, "note": f"claude leg skipped: {e}"}


def leg_jev(results: dict):
    from jarvis.jev import engine
    eng = engine()
    if not eng.available:
        results["jev"] = {"n": 0, "note": "no TYPESAFE_API_KEY — JEV network legs not measured; router falls through to OCR/vision"}
        return
    # real, but guarded by the budget ceiling inside .pick(); a couple of tiny calls only
    cands = [{"id": 1, "role": "button", "name": "Save"}, {"id": 2, "role": "button", "name": "Save as"},
             {"id": 3, "role": "button", "name": "Cancel"}]
    def _warm():
        eng._http()  # establish the pooled httpx client (TLS/DNS) without a POST
    results["jev_client_setup"] = summarize(timed(_warm, iters=3, warmup=0))
    try:
        results["jev_pick_warm"] = summarize(timed(
            lambda: eng.pick("save the file", cands, {"title": "Untitled - Notepad", "process": "notepad.exe"}),
            iters=3, warmup=1))
    except Exception as e:  # noqa: BLE001
        results["jev_pick_warm"] = {"n": 0, "note": str(e)}


# ------------------------------------------------------------------ endpointing: legacy vs adaptive (synthetic)
def _envelope(segments: list[tuple[float, bool]]) -> list[tuple[float, float]]:
    """Build (t, rms) frames at FRAME_S. segments = [(duration_s, is_speech), …]."""
    frames, t = [], 0.0
    for dur, speech in segments:
        n = int(round(dur / FRAME_S))
        for _ in range(n):
            frames.append((t, 0.05 if speech else 0.001))
            t += FRAME_S
    return frames


def _finalize_time(frames, adaptive: bool) -> tuple[float | None, float | None]:
    """Run an endpointer over the frames. Returns (finalize_t, last_speech_t)."""
    last_speech = None
    if adaptive:
        ep = AdaptiveEndpointer(EndpointConfig())
        for t, rms in frames:
            if rms > ep.cfg.energy_threshold:
                last_speech = t
            if ep.feed(rms, t) is Turn.FINALIZED:
                return t, last_speech
        return None, last_speech
    # legacy fixed rule
    t0 = frames[0][0] if frames else 0.0
    spoke, last_voice = False, None
    for t, rms in frames:
        if rms > 0.012:
            spoke, last_voice = True, t
            last_speech = t
        if legacy_should_finalize(spoke, t, last_voice, t0):
            return t, last_speech
    return None, last_speech


def leg_endpointing(results: dict):
    scenarios = {
        # a crisp short command ("Open Chrome."): how long AFTER speech ends until we finalize (lower = snappier)
        "command_short": [(0.3, False), (1.1, True), (2.0, False)],
        # a hesitant correction ("Open Chrome… and… Edge instead"): a first short gap establishes hesitation, then a
        # 1.05 s gap that legacy (fixed 0.9 s) cuts through but adaptive tolerates because the speaker is hesitating
        "hesitation": [(0.3, False), (0.8, True), (0.5, False), (0.5, True), (1.05, False), (0.7, True), (2.0, False)],
        # a longer dictated sentence
        "long_sentence": [(0.3, False), (3.5, True), (2.0, False)],
    }
    out = {}
    for name, segs in scenarios.items():
        frames = _envelope(segs)
        lf, lspeech = _finalize_time(frames, adaptive=False)
        af, aspeech = _finalize_time(frames, adaptive=True)
        row = {
            "legacy_tail_ms": None if (lf is None or lspeech is None) else round((lf - lspeech) * 1000),
            "adaptive_tail_ms": None if (af is None or aspeech is None) else round((af - aspeech) * 1000),
            "legacy_premature_cut": _premature(name, frames, adaptive=False),
            "adaptive_premature_cut": _premature(name, frames, adaptive=True),
        }
        out[name] = row
    results["endpointing"] = out


def _premature(name: str, frames, adaptive: bool) -> bool:
    """A finalize that lands during a segment that is still followed by more speech = a cut-off."""
    if name != "hesitation":
        return False
    f, _ = _finalize_time(frames, adaptive)
    if f is None:
        return False
    # in the hesitation scenario the last speech ends at 0.3+0.8+0.5+0.5+1.05+0.7 = 3.85 s; finalizing before = a cut
    return f < 3.85


# ------------------------------------------------------------------ driver
def run(label: str, claude: bool = False) -> dict:
    results: dict = {}
    print(f"[latency:{label}] endpointing (synthetic)…")
    leg_endpointing(results)
    print(f"[latency:{label}] context build…")
    leg_context(results)
    print(f"[latency:{label}] uia / ocr / screenshot (read-only)…")
    leg_uia_ocr_screen(results)
    print(f"[latency:{label}] jev…")
    leg_jev(results)
    if claude:
        print(f"[latency:{label}] claude (conversational, no desktop)…")
        leg_claude(results)
    return {"label": label, "ts": time.time(), "machine_settings": {
        "prisma_model": config.SETTINGS.get("prisma_model"), "timbre_model": config.SETTINGS.get("timbre_model"),
        "response_mode": config.SETTINGS.get("response_mode")}, "legs": results}


def to_md(data: dict) -> str:
    L = data["legs"]
    lines = [f"# JARVIS latency — {data['label']}", "",
             f"Generated {time.strftime('%Y-%m-%d %H:%M', time.localtime(data['ts']))} · "
             f"Gnani Prisma={data['machine_settings']['prisma_model']} · Timbre={data['machine_settings']['timbre_model']}.",
             "",
             "Offline-measurable legs only (no mouse / no audio / no app launch). The live end-to-end wall-clock chain "
             "and live barge-in timing are measured separately when the user is away — see the report.", ""]
    # endpointing
    ep = L.get("endpointing", {})
    if ep:
        lines += ["## End-of-turn detection (legacy fixed 0.9 s vs adaptive)", "",
                  "| scenario | legacy tail ms | adaptive tail ms | legacy cut-off | adaptive cut-off |",
                  "|---|---|---|---|---|"]
        for name, r in ep.items():
            lines.append(f"| {name} | {r['legacy_tail_ms']} | {r['adaptive_tail_ms']} | "
                         f"{'YES' if r['legacy_premature_cut'] else 'no'} | {'YES' if r['adaptive_premature_cut'] else 'no'} |")
        lines.append("")
    # timed legs
    lines += ["## Timed legs (ms)", "", "| leg | n | p50 | p75 | p95 | min | max |", "|---|---|---|---|---|---|---|"]
    for k, v in L.items():
        if k == "endpointing" or not isinstance(v, dict):
            continue
        if "once_ms" in v:
            lines.append(f"| {k} | 1 | {v['once_ms']} | — | — | — | — |")
        elif v.get("n"):
            lines.append(f"| {k} | {v['n']} | {v.get('p50')} | {v.get('p75')} | {v.get('p95')} | {v.get('min')} | {v.get('max')} |")
        else:
            lines.append(f"| {k} | 0 | — | — | — | — | _{v.get('note', 'n/a')}_ |")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--label", default="before", choices=["before", "after"])
    ap.add_argument("--claude", action="store_true", help="also measure Claude first-clause vs full-turn (spends quota)")
    args = ap.parse_args()
    data = run(args.label, args.claude)
    (HERE / f"{args.label}.json").write_text(json.dumps(data, indent=2), encoding="utf-8")
    (HERE / f"{args.label}.md").write_text(to_md(data), encoding="utf-8")
    print(f"\n-> wrote {args.label}.json / {args.label}.md ({len(data['legs'])} legs)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
