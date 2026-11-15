"""Learning benchmark (§31, §32): repeat an ambiguous-target task class and show that, as experience accumulates, the
learned policy serves the correct control locally and the JEV call is avoided — with verified success held at 100 %.

No desktop is driven and no network is used: a deterministic *oracle* stands in for the resolver that would pick the
correct control (as JEV would), and the learned policy is asked, per episode, whether it can already serve that context.
This isolates the learning signal (JEV-calls-avoided vs episode) honestly. Run:  python -m benchmarks.learning
"""
from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from jarvis.learning import features                      # noqa: E402
from jarvis.learning.manager import LearningManager       # noqa: E402

# a small suite of ambiguous contexts, each with a "correct" control the oracle (≈JEV) would choose
TASKS = [
    ("save the file", [{"id": 1, "role": "button", "name": "Save"}, {"id": 2, "role": "button", "name": "Save as"},
                       {"id": 3, "role": "button", "name": "Cancel"}], {"process": "notepad.exe"}, "button|save"),
    ("open the downloads folder", [{"id": 1, "role": "listitem", "name": "Downloads"},
                                   {"id": 2, "role": "listitem", "name": "Documents"},
                                   {"id": 3, "role": "listitem", "name": "Desktop"}], {"process": "explorer.exe"}, "listitem|downloads"),
    ("send the message", [{"id": 1, "role": "button", "name": "Send"}, {"id": 2, "role": "button", "name": "Attach"},
                          {"id": 3, "role": "button", "name": "Emoji"}], {"process": "whatsapp.exe"}, "button|send"),
]
EPISODES = 12


def run() -> dict:
    _tmp_root = ROOT / ".cache" / "tmp"  # keep scratch on the D: project drive, never the C: system temp
    _tmp_root.mkdir(parents=True, exist_ok=True)
    tmp = Path(tempfile.mkdtemp(prefix="jarvis_learn_bench_", dir=str(_tmp_root)))
    try:
        m = LearningManager(exp_dir=tmp / "exp.db", pol_dir=tmp / "pol")
        m.enabled = True
        per_episode = []
        for ep in range(1, EPISODES + 1):
            jev_calls = served = correct = 0
            for goal, cands, win, right in TASKS:
                fp = features.context_fingerprint(goal, cands, win)
                sig_map = features.signatures(cands)
                m.record_decision(goal, cands, win)
                el_id, conf, sig = m.serve(fp, sig_map)     # production policy's confident pick, or defer
                if el_id is not None:                        # learned local decision → JEV avoided
                    served += 1
                    m.note_served(sig, conf)
                    verified = (sig == right)
                    m.record_outcome(next(c for c in cands if c["id"] == el_id), "policy", verified)
                else:                                        # defer → the oracle (≈JEV) resolves it, and it verifies
                    jev_calls += 1
                    chosen = next(c for c in cands if features.candidate_signature(c) == right)
                    m.record_outcome(chosen, "jev", True)
                    verified = True
                correct += int(verified)
            m.flush()                                        # background worker would do this; forced here per episode
            per_episode.append({"episode": ep, "jev_calls": jev_calls, "served_locally": served,
                                "success": correct, "policy_version": m.production.version})
        st = m.status()
        m.stop()
        early = per_episode[0]
        late = per_episode[-1]
        result = {"episodes": EPISODES, "tasks_per_episode": len(TASKS), "per_episode": per_episode,
                  "early": early, "late": late, "status": st,
                  "jev_calls_first": early["jev_calls"], "jev_calls_last": late["jev_calls"],
                  "success_first": f"{early['success']}/{len(TASKS)}", "success_last": f"{late['success']}/{len(TASKS)}",
                  "promotions": st["promotions"]}
        return result
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    import json
    r = run()
    print("episode : jev_calls  served_local  success  prod_v")
    for e in r["per_episode"]:
        print(f"  {e['episode']:>2}    :    {e['jev_calls']:>2}         {e['served_locally']:>2}         {e['success']}/{r['tasks_per_episode']}      v{e['policy_version']}")
    print()
    print(f"JEV calls: episode 1 = {r['jev_calls_first']}  →  episode {r['episodes']} = {r['jev_calls_last']}  "
          f"(verified success {r['success_first']} → {r['success_last']}, {r['promotions']} promotions)")
    out = ROOT / "benchmarks" / "learning_results.json"
    out.write_text(json.dumps(r, indent=2), encoding="utf-8")
    print(f"→ {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
