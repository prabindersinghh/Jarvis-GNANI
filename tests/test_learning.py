"""RL / perpetual-learning subsystem (§43-45): experience, reward, policy, candidate eval, promotion, rollback,
persistence/crash-recovery, privacy, user corrections, JEV-avoidance at the router, safety/budget boundaries."""
from __future__ import annotations

import json
import time

import pytest

from jarvis.learning import features
from jarvis.learning import reward as rewardmod
from jarvis.learning.experience import ExperienceStore
from jarvis.learning.manager import LearningManager
from jarvis.learning.policy import BanditPolicy


@pytest.fixture
def mgr(tmp_path):
    m = LearningManager(exp_dir=tmp_path / "exp.db", pol_dir=tmp_path / "pol")
    m.enabled = True
    yield m
    m.stop()


# ---- experience store -----------------------------------------------------------
def test_experience_records_and_redacts_and_bounds(tmp_path):
    st = ExperienceStore(path=tmp_path / "e.db", max_rows=5)
    st.record({"context_fingerprint": "fp", "chosen_action": "button|save", "reward": 1.0, "verification_result": 1,
               "success": 1, "routing_tier": "policy"})
    st.record({"context_fingerprint": "fp", "chosen_action": "edit|password field with token sk-ABCDEFGHIJKLMNOP",
               "reward": -1.0})
    rows = st.recent(10)
    assert rows[0]["chosen_action"] == "button|save"
    # secret-bearing chosen_action is dropped, and any redactor runs on stored text
    assert "sk-ABCDEFGHIJKLMNOP" not in json.dumps(rows)
    for i in range(10):
        st.record({"context_fingerprint": "fp", "chosen_action": f"a{i}", "reward": 0.0})
    assert st.count() <= 5  # bounded compaction


# ---- reward ---------------------------------------------------------------------
def test_reward_verification_is_authority():
    assert rewardmod.compute(True, "uia").value > 0.9
    assert rewardmod.compute(False, "uia").value < -0.9              # verification failure = strong negative
    assert rewardmod.compute(True, "jev").value < rewardmod.compute(True, "uia").value  # jev costs more
    assert rewardmod.compute(True, "uia", blocked=True).value < -1.0 and rewardmod.compute(True, "uia", blocked=True).blocked
    assert rewardmod.compute(True, "policy", user_correction=True).value < -1.0
    # faster than baseline earns a bonus; slower loses a little
    fast = rewardmod.compute(True, "policy", latency_ms=100, baseline_ms=1000).value
    slow = rewardmod.compute(True, "policy", latency_ms=2000, baseline_ms=1000).value
    assert fast > slow


# ---- policy: update, confidence, pick ------------------------------------------
def test_policy_pick_needs_samples_and_confidence():
    p = BanditPolicy()
    # one success is not enough (min_n)
    p.update("fp", "button|save", 1.2, True)
    assert p.pick("fp", {"button|save", "button|cancel"})[0] is None
    for _ in range(5):
        p.update("fp", "button|save", 1.2, True)
    sig, conf = p.pick("fp", {"button|save", "button|cancel"})
    assert sig == "button|save" and conf >= 0.72
    # an arm that fails often is never served
    for _ in range(6):
        p.update("fp", "button|cancel", -1.2, False)
    assert p.pick("fp", {"button|cancel"})[0] is None


# ---- candidate evaluation + promotion (§19-20) ---------------------------------
def _train_success(m, fp_ctx, good="button|save", n=8):
    """Simulate n verified outcomes where `good` was taken and worked, for a fixed context."""
    cands = [{"id": 1, "role": "button", "name": "Save"}, {"id": 2, "role": "button", "name": "Cancel"}]
    for _ in range(n):
        import threading
        m.record_decision("save the file", cands, {"process": "notepad.exe", "title": "T"})
        # pretend JEV/normal path chose the good arm and it verified
        m.record_outcome({"id": 1, "role": "button", "name": "Save"}, "jev", True)


def test_promotion_on_measured_improvement(mgr):
    _train_success(mgr, None, n=10)
    v0 = mgr.production.version
    mgr.flush()
    assert mgr.production.version > v0, "candidate that clearly beats production was not promoted"
    assert mgr.counters["promotions"] == 1
    # parent kept for rollback
    assert mgr.parent.version == v0
    # now production serves the learned arm → JEV would be avoided
    fp = features.context_fingerprint("save the file", [{"id": 1, "role": "button", "name": "Save"},
                                                        {"id": 2, "role": "button", "name": "Cancel"}],
                                      {"process": "notepad.exe"})
    el_id, conf, sig = mgr.serve(fp, {"button|save": 1, "button|cancel": 2})
    assert el_id == 1 and sig == "button|save" and conf >= 0.72


def test_bad_candidate_is_not_promoted(mgr):
    # candidate records only failures → its estimated value is not above production → no promotion
    cands = [{"id": 1, "role": "button", "name": "Save"}, {"id": 2, "role": "button", "name": "Cancel"}]
    for _ in range(10):
        mgr.record_decision("save", cands, {"process": "notepad.exe"})
        mgr.record_outcome({"id": 2, "role": "button", "name": "Cancel"}, "jev", False)  # wrong + unverified
    v0 = mgr.production.version
    mgr.flush()
    assert mgr.production.version == v0, "a candidate with only failures was promoted"
    assert mgr.counters["promotions"] == 0


# ---- rollback (§21) -------------------------------------------------------------
def test_regressed_policy_rolls_back(mgr):
    _train_success(mgr, None, n=10)
    mgr.flush()
    assert mgr.production.version >= 2 and mgr.counters["promotions"] == 1
    good_v = mgr.production.version
    # production now serves; simulate live regression: served picks keep failing verification
    fp = features.context_fingerprint("save the file", [{"id": 1, "role": "button", "name": "Save"},
                                                        {"id": 2, "role": "button", "name": "Cancel"}],
                                      {"process": "notepad.exe"})
    for _ in range(8):
        mgr.record_decision("save the file", [{"id": 1, "role": "button", "name": "Save"},
                                              {"id": 2, "role": "button", "name": "Cancel"}], {"process": "notepad.exe"})
        mgr.note_served("button|save", 0.8)                 # production served this pick
        mgr.record_outcome({"id": 1, "role": "button", "name": "Save"}, "policy", False)  # it now fails
    mgr.flush()
    assert mgr.counters["rollbacks"] >= 1, "regressed production did not roll back"
    assert mgr.production.version <= good_v  # restored to (or below) the last known-good parent


# ---- persistence + crash recovery (§23) ----------------------------------------
def test_persistence_and_corrupt_recovery(tmp_path):
    m = LearningManager(exp_dir=tmp_path / "e.db", pol_dir=tmp_path / "pol")
    for _ in range(8):
        m.record_decision("save", [{"id": 1, "role": "button", "name": "Save"}, {"id": 2, "role": "button", "name": "Cancel"}], {"process": "notepad.exe"})
        m.record_outcome({"id": 1, "role": "button", "name": "Save"}, "jev", True)
    m.flush(); m.stop()
    pv = m.production.version
    # restart → state reloads
    m2 = LearningManager(exp_dir=tmp_path / "e.db", pol_dir=tmp_path / "pol")
    assert m2.production.version == pv and m2.production.stats, "learned state lost across restart"
    m2.stop()
    # corrupt the production file → loader falls back (no crash, no corruption of parent)
    (tmp_path / "pol" / "production.json").write_text("{ this is : not json", encoding="utf-8")
    m3 = LearningManager(exp_dir=tmp_path / "e.db", pol_dir=tmp_path / "pol")
    assert m3.production is not None  # recovered (empty or parent), did not raise
    m3.stop()


# ---- user correction is a strong signal (§8) -----------------------------------
def test_user_correction_shifts_policy(mgr):
    win = {"process": "chrome.exe"}
    wrong = {"id": 1, "role": "button", "name": "Reply"}
    right = {"id": 2, "role": "button", "name": "Forward"}
    for _ in range(4):
        mgr.record_correction("forward the email", wrong, right, win)
    fp = features.context_fingerprint("forward the email", [wrong, right], win)
    assert mgr.candidate.value(fp, "button|forward") > mgr.candidate.value(fp, "button|reply")
    assert mgr.counters["corrections"] == 4


# ---- router integration: a learned pick avoids the JEV call (§11, §44.6) --------
class _FakeObs:
    def __init__(self, els, window):
        self.elements = els
        self.window = window
        self.fingerprint = "fp"
        self.capture_id = 1

    def element(self, i):
        return next((e for e in self.elements if e["id"] == i), None)


def test_router_uses_learned_pick_and_skips_jev(mgr, monkeypatch):
    from jarvis.learning.hook import LearningHook
    from jarvis.orchestrator import hooks, router
    hooks.clear()
    hooks.register(LearningHook(mgr))
    els = [{"id": 1, "role": "button", "name": "Save", "bounds": [0, 0, 50, 20], "patterns": ["Invoke"]},
           {"id": 2, "role": "button", "name": "Save", "bounds": [300, 300, 350, 320], "patterns": ["Invoke"]}]
    win = {"process": "app.exe", "title": "T", "hwnd": 1}
    obs = _FakeObs(els, win)
    # JEV must NOT be called once the policy is confident; make it explode if consulted
    monkeypatch.setattr(router, "jev_engine", lambda: (_ for _ in ()).throw(AssertionError("JEV was called")))
    # teach the policy that candidate #1's signature works here (same fingerprint the router will compute)
    fp = features.context_fingerprint("click Save", els, win)
    for _ in range(6):
        mgr.production.update(fp, "button|save", 1.2, True)
    el, tier, info = router.resolve_target("Save", goal="click Save", obs=obs, allow_jev=True, window="T")
    assert tier == "policy" and el["id"] in (1, 2), (tier, info)
    assert mgr.counters["jev_avoided"] >= 1
    hooks.clear()


# ---- safety boundary stays outside RL (§28) ------------------------------------
def test_hook_never_serves_high_impact_control(mgr):
    from jarvis.learning.hook import LearningHook
    hook = LearningHook(mgr)
    win = {"process": "explorer.exe", "title": "T"}
    danger = [{"id": 1, "role": "button", "name": "Delete permanently"}, {"id": 2, "role": "button", "name": "Cancel"}]
    fp = features.context_fingerprint("remove it", danger, win)
    for _ in range(6):
        mgr.production.update(fp, "button|delete permanently", 1.2, True)   # even if "learned", it must not auto-serve
    assert hook.before_decide("remove it", danger, {"window": win}) is None
    # a blocked outcome yields a hard-negative, never a bypass
    r = rewardmod.compute(True, "policy", blocked=True)
    assert r.blocked and r.value < 0


def test_status_and_daily_summary(mgr):
    _train_success(mgr, None, n=6)
    st = mgr.status()
    assert st["enabled"] and "production_version" in st and st["experiences"] >= 6
    summ = mgr.daily_summary()
    assert summ["tasks"] >= 6 and 0 <= (summ["success_rate"] or 0) <= 1
