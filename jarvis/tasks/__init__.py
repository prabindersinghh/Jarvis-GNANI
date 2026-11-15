"""JARVIS durable task engine + event bus (OpenMuse/AG-UI integration).

One authoritative task engine and one normalized event stream, sitting as an orchestration/presentation layer ABOVE
JARVIS's existing brain (Claude/JEV/RL), execution (Windows engine) and experience (memory/daily conversation). It
never duplicates or replaces those — see docs/OPENMUSE_INTEGRATION_AUDIT.md.
"""
from .events import Event, EventBus, EventType, bus
from .intent import Intent, classify
from .manager import TaskManager, manager
from .store import Task, TaskState, TaskStore

__all__ = ["Event", "EventBus", "EventType", "bus", "Intent", "classify",
           "TaskManager", "manager", "Task", "TaskState", "TaskStore"]
