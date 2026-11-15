"""JARVIS perpetual learning — contextual-bandit policy over the router's decision hook.

Claude stays the reasoning brain and JEV the reflex layer; this package learns AROUND them: which local strategy
historically produced the highest VERIFIED reward for a context, so JARVIS needs fewer paid/heavy decisions over time.
Constrained, verified, reversible, local-first (§41). Safety and the JEV ₹1,000 budget sit outside it and cannot be
learned around.
"""
from .manager import LearningManager, manager  # noqa: F401


def install() -> "LearningManager":
    """Register the live learning hook with the router and start the background worker. Idempotent."""
    from ..orchestrator import hooks
    from .hook import LearningHook
    m = manager()
    if not getattr(m, "_installed", False):
        hooks.register(LearningHook(m))
        m._installed = True
        m.start()
    return m
