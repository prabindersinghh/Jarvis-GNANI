# JARVIS perpetual learning (RL layer) — 2026-09-21

JARVIS now improves from real usage automatically and indefinitely, while staying constrained, verified, reversible
and local-first. Claude stays the reasoning brain and JEV the reflex layer; JARVIS learns AROUND them so known
situations need fewer paid/heavy decisions over time. **No RL rewrites source, safety, budget, privacy, or Claude.**

## Learning architecture
```
router.resolve_target  ──ambiguous candidates──►  hooks.before_decide ─► LearningHook ─► production policy.pick
   (after deterministic/unique/heuristic, BEFORE JEV)                         │ confident+safe? → serve local id (JEV avoided)
                                                                              └ else None → JEV (through the budget guard)
execute → verify ─► hooks.after_outcome ─► reward.compute ─► experience row ─► candidate.update ─► background worker
                                                                                    │ evaluate candidate vs production
                                                                                    ├ better (margin+samples) → PROMOTE (parent kept)
                                                                                    └ live regression → ROLLBACK to parent
```
The RL layer sits **above** JEV: it can only reduce JEV usage or defer; it never requests JEV directly, so the
₹1,000/month budget guard remains the sole gate on JEV spend and cannot be bypassed (§12).

## Algorithm and why
**Contextual bandit** (Phase A, §4) over the router's existing discrete candidate arms — a per-(context-fingerprint,
arm) Beta/Bernoulli estimator with a Wilson lower-confidence bound. It is the simplest method that produces a
measurable, safe improvement on the actual decision the router exposes (which control to pick for an ambiguous target),
needs no GPU/neural net, and confines the learned action to the existing safe candidate set. Heavier RL (Phases B–D)
is deliberately not added — measurements did not require it.

## Policy state (`data/policies/`)
`production.json` (serves live decisions, conservative), `candidate.json` (learns online), `parent.json` (last
known-good for rollback). Each carries `version`, `parent`, `created`, `metrics`. Writes are atomic (temp + `os.replace`
+ fsync); a corrupt/partial file falls back to parent/empty on load (crash-safe, §23).

## Experience schema (`data/memory/experiences/experiences.db`, SQLite)
`experience_id, ts, date, session_id, task_id, application, window_context, action_type, routing_tier, policy_version,
candidate_count, chosen_action, confidence, jev_used, ocr_used, vision_used, claude_used, batch_used, latency_ms,
verification_result, success, failure_reason, retry_count, fallback_count, user_correction, safety_classification,
reward, context_fingerprint, source`. Bounded (compacts to `max_rows`). Every text field is redacted; secret-bearing
chosen actions are dropped (`[sensitive]`). Distinct from `jarvis.memory` (preferences/workflows) and `jarvis.history`
(transcript).

## Reward (`reward.py`) — verification is the authority (§7, §9)
`verified` dominates (±1.2); + first-attempt bonus; − retries/fallbacks; − tier cost (JEV 0.06, OCR 0.04, vision 0.12);
± latency vs baseline (bounded); user-correction = strong negative (−1.5); blocked/denied = hard negative and *flagged*
so the policy is never trained toward a boundary bypass. Clamped to [−2, 2]. "Input was sent" is never success.

## Features (`features.py`) — lightweight, private (§26)
Context fingerprint = sha1 over (application, top goal keywords, candidate-count bucket, sorted candidate signatures).
Arm = candidate signature `role|name` (redacted). No screenshots/raw content enter the policy.

## Memory / workflow / JEV integration (§10, §11, §15, §17)
Kept distinct: MEMORY = what happened / preferences / known workflows; POLICY = what to choose now; JEV = which
structured candidate right now; CLAUDE = novel reasoning. The learned policy reads experiences (not memory), and the
existing `memory.learn_routing` / `learn_batch` continue unchanged. Workflow induction remains in `jarvis.memory`
(successful `computer_run` batches → reusable workflows), which the policy's context features can draw on.

## Safety and budget outside RL (§12, §28)
- `LearningHook.before_decide` returns only an id from the router's existing safe pool, re-checks the candidate with
  `safety.classify_click`, and refuses to auto-serve any non-SAFE (high-impact) control — those still go through the
  human confirm path. It never invents an action and never calls JEV.
- A blocked/denied action never executes → never earns positive reward → the policy cannot learn to choose it.
- JEV still flows router → budget guard; the RL layer can lower JEV usage but cannot disable or exceed the ₹1,000 ceiling.

## Background worker (§22)
A daemon thread at below-normal priority: consumes experience signals, evaluates the candidate against production on
recent experience (direct-method value: serving a reliable local arm scores above deferring to a paid JEV call),
promotes on a ≥0.05 estimated-value margin with ≥6 supporting samples, monitors live production reward and rolls back
on a ≥0.4 regression, checkpoints atomically, and compacts storage. It does not block active task execution and shuts
down gracefully. The orb shows a subtle **learning** state only while the worker is actually processing.

## Observability (§38, §39)
`manager.status()` (production/candidate/parent versions, contexts, arms, experiences, JEV-avoided, promotions,
rollbacks, live reward) is surfaced in `computer_status` and as a `LEARNING` row in the orb status strip. A local
`daily_summary` (tasks, success rate, corrections, JEV calls vs avoided, latency, version) is written to
`data/metrics/daily_<date>.json` on shutdown.

## Benchmark (§31, §32) — `python -m benchmarks.learning` → benchmarks/learning_results.json
12 episodes over 3 ambiguous contexts (Notepad Save, Explorer Downloads, WhatsApp Send). Measured:

| | episode 1 | episode 12 |
|---|---|---|
| JEV calls / episode | **3** | **0** |
| served locally / episode | 0 | 3 |
| verified success | 3/3 | 3/3 |
| production version | v1 | v2 (promoted at ep 5) |

JARVIS learned to make the previously JEV-resolved decisions locally, verified success held at 100 %, after one
measured promotion. (Honest isolation: an oracle supplies the correct control only when the policy defers, as JEV would;
once confident the policy serves locally and JEV is not consulted.)

## Tests (`tests/test_learning.py`, 11) — all 12 required demonstrations (§44)
experience record+redaction+bounding; reward (verification authority, JEV cost, blocked, correction, latency); policy
sample/confidence gate; promotion on measured improvement; **bad candidate rejected**; **regressed policy rolled back**;
persistence + **corrupt-file recovery**; user-correction shifts policy; **router uses learned pick and skips JEV** (JEV
made to raise if called); hook never serves a high-impact control; status + daily summary. 62 non-live tests green
(unit + budget + memory + history + learning); JEV budget and safety tests unchanged; app boots the worker and persists
`data/policies/`.

## Limitations
- Phase A only (contextual bandit); sequence/workflow RL (Phases B–D) is scaffolded via the experience store but not
  activated — no measured need yet.
- The concrete learned target today is ambiguous target-selection (the arm the router hook exposes), which is also the
  clearest JEV-avoidance win; other policy targets (WAIT/BATCH/FALLBACK) record experience but are not yet served.
- The live end-to-end JEV-avoidance was validated at the router level with a fake observation and in the benchmark, not
  by driving the real desktop repeatedly (that fights the user's input); run `pytest tests -m live` when away to
  exercise it on real apps.

---

