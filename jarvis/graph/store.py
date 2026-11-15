r"""JARVIS memory graph — a local, private knowledge graph of JARVIS's OWN experience (2026-09-22).

Purpose (user request): "make a graph of jarvis and everything which will happen from now onwards, memory and all, so
it is easy for him to recall." Every turn, JARVIS links what happened — which day/session, what the user asked, which
app it touched, which tools ran, whether it was verified — into a small typed graph it can query for recall. This is
distinct from `graphify` (a batch *codebase* grapher, see docs/GRAPH.md): this graph is incremental, updated live, and
holds the user's activity, so it is **local-only, redacted, never networked**, and it exports to graphify's `graph.json`
shape so the same viewer/query tooling can visualize it.

Storage: `data/graph/memory_graph.json` (atomic write + fsync; a corrupt/partial file recovers to empty on load, like
the learning store). Bounded to `max_nodes`/`max_edges` (oldest pruned). Every text label is redacted before storage;
secret-bearing content is dropped. No screenshots, no raw transcripts — short labels and counts only.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from pathlib import Path

from .. import config

log = logging.getLogger("jarvis.graph")

NODE_TYPES = ("date", "session", "turn", "app", "tool", "memory", "outcome", "topic")


def _redact(text: str) -> str:
    try:
        from ..safety import privacy
        return privacy.redact_text(text or "")
    except Exception:  # noqa: BLE001
        return text or ""


class MemoryGraph:
    def __init__(self, path: Path | None = None, max_nodes: int = 5000, max_edges: int = 20000):
        self.path = Path(path) if path else (config.DATA / "graph" / "memory_graph.json")
        self.max_nodes, self.max_edges = max_nodes, max_edges
        self._lock = threading.RLock()
        self.nodes: dict[str, dict] = {}
        self.edges: dict[str, dict] = {}
        self._load()

    # -- persistence --------------------------------------------------------------
    def _load(self):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            self.nodes = d.get("nodes", {})
            self.edges = d.get("edges", {})
        except Exception:  # noqa: BLE001 — missing/corrupt → start empty (never crash the app)
            self.nodes, self.edges = {}, {}

    def save(self):
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            payload = {"version": 1, "updated": time.time(), "nodes": self.nodes, "edges": self.edges}
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(payload, f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.path)

    # -- primitives ---------------------------------------------------------------
    @staticmethod
    def _nid(ntype: str, key: str) -> str:
        return f"{ntype}:{key}"

    def add_node(self, ntype: str, key: str, label: str | None = None, **attrs) -> str:
        key = (_redact(str(key))[:80]).strip() or "?"
        nid = self._nid(ntype, key)
        with self._lock:
            n = self.nodes.get(nid)
            now = time.time()
            if n is None:
                n = {"type": ntype, "key": key, "label": _redact(label or key)[:120], "attrs": {},
                     "count": 0, "first_seen": now, "last_seen": now}
                self.nodes[nid] = n
                self._prune_nodes()
            n["count"] += 1
            n["last_seen"] = now
            for k, v in attrs.items():
                if v is not None:
                    n["attrs"][k] = v if not isinstance(v, str) else _redact(v)[:120]
            return nid

    def add_edge(self, src: str, dst: str, rel: str) -> None:
        if not src or not dst or src == dst:
            return
        eid = f"{src}|{rel}|{dst}"
        with self._lock:
            e = self.edges.get(eid)
            now = time.time()
            if e is None:
                self.edges[eid] = {"src": src, "dst": dst, "rel": rel, "count": 1, "last_seen": now}
                self._prune_edges()
            else:
                e["count"] += 1
                e["last_seen"] = now

    def _prune_nodes(self):
        if len(self.nodes) <= self.max_nodes:
            return
        drop = sorted(self.nodes, key=lambda k: self.nodes[k]["last_seen"])[: len(self.nodes) - self.max_nodes]
        for k in drop:
            self.nodes.pop(k, None)
        self.edges = {k: e for k, e in self.edges.items() if e["src"] in self.nodes and e["dst"] in self.nodes}

    def _prune_edges(self):
        if len(self.edges) <= self.max_edges:
            return
        drop = sorted(self.edges, key=lambda k: self.edges[k]["last_seen"])[: len(self.edges) - self.max_edges]
        for k in drop:
            self.edges.pop(k, None)

    # -- high-level recording (called from the app, once per turn) ----------------
    def record_turn(self, user_text: str, *, session_id: str | None = None, app: str | None = None,
                    tools: dict | None = None, verified: bool | None = None, date: str | None = None) -> str:
        """Link a conversation turn into the graph: date → session → turn → {app, tools, outcome}. Redacts everything."""
        with self._lock:
            date = date or time.strftime("%Y-%m-%d")
            dnode = self.add_node("date", date)
            snode = self.add_node("session", session_id or date, label=f"session {session_id or date}")
            self.add_edge(dnode, snode, "has_session")
            label = (user_text or "").strip()
            tnode = self.add_node("turn", f"{int(time.time()*1000)}", label=label[:120] or "(turn)", text=label[:200])
            self.add_edge(snode, tnode, "has_turn")
            for w in _keywords(label):
                self.add_edge(tnode, self.add_node("topic", w), "about")
            if app:
                self.add_edge(tnode, self.add_node("app", app.lower(), label=app), "used_app")
            for name, cnt in (tools or {}).items():
                short = str(name).replace("mcp__jarvis__", "")
                self.add_edge(tnode, self.add_node("tool", short, count_hint=cnt), "ran_tool")
            if verified is not None:
                self.add_edge(tnode, self.add_node("outcome", "verified" if verified else "failed"),
                              "resulted_in")
            return tnode

    def record_memory(self, text: str, kind: str = "memory") -> str:
        with self._lock:
            m = self.add_node("memory", f"{kind}:{int(time.time()*1000)}", label=(text or "")[:120], kind=kind)
            for w in _keywords(text or ""):
                self.add_edge(m, self.add_node("topic", w), "about")
            return m

    # -- recall -------------------------------------------------------------------
    def neighbors(self, nid: str) -> list[dict]:
        out = []
        for e in self.edges.values():
            if e["src"] == nid and e["dst"] in self.nodes:
                out.append({"rel": e["rel"], "node": self.nodes[e["dst"]], "count": e["count"]})
            elif e["dst"] == nid and e["src"] in self.nodes:
                out.append({"rel": f"<-{e['rel']}", "node": self.nodes[e["src"]], "count": e["count"]})
        return out

    def recall(self, term: str, limit: int = 10) -> list[dict]:
        """Find nodes whose key/label/attrs mention `term` (case-insensitive), most-recent first — for quick recall."""
        t = _redact(term).lower().strip()
        hits = []
        with self._lock:
            for nid, n in self.nodes.items():
                hay = f"{n['key']} {n.get('label', '')} {json.dumps(n.get('attrs', {}))}".lower()
                if t and t in hay:
                    hits.append({"id": nid, **n, "links": len(self.neighbors(nid))})
        hits.sort(key=lambda h: h["last_seen"], reverse=True)
        return hits[:limit]

    def stats(self) -> dict:
        with self._lock:
            by_type: dict[str, int] = {}
            for n in self.nodes.values():
                by_type[n["type"]] = by_type.get(n["type"], 0) + 1
            return {"nodes": len(self.nodes), "edges": len(self.edges), "by_type": by_type}

    # -- graphify-compatible export ----------------------------------------------
    def export_graphify(self, path: Path | None = None) -> Path:
        """Write a `graph.json` in graphify's node/edge shape so its viewer/query tooling can visualize this graph.
        Local file only — nothing is uploaded."""
        path = Path(path) if path else (self.path.parent / "graph.json")
        with self._lock:
            nodes = [{"id": nid, "type": n["type"], "label": n.get("label", n["key"]),
                      "confidence": "EXTRACTED", "count": n["count"]} for nid, n in self.nodes.items()]
            edges = [{"source": e["src"], "target": e["dst"], "type": e["rel"],
                      "confidence": "EXTRACTED", "count": e["count"]} for e in self.edges.values()]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"nodes": nodes, "edges": edges,
                                    "meta": {"source": "jarvis-memory-graph", "generated": time.time()}},
                                   ensure_ascii=False, indent=2), encoding="utf-8")
        return path


_STOP = {"the", "a", "an", "and", "or", "to", "of", "in", "on", "for", "with", "my", "me", "it", "is", "then",
         "please", "jarvis", "open", "go", "then", "that", "this", "now", "can", "you", "i", "at", "by", "up"}


def _keywords(text: str, n: int = 4) -> list[str]:
    """Cheap topic keywords (redacted, de-noised) — no model, purely local."""
    words = [w.strip(".,!?;:'\"").lower() for w in _redact(text).split()]
    seen, out = set(), []
    for w in words:
        if len(w) >= 4 and w not in _STOP and w.isalpha() and w not in seen:
            seen.add(w)
            out.append(w)
        if len(out) >= n:
            break
    return out


_graph: MemoryGraph | None = None


def graph() -> MemoryGraph:
    global _graph
    if _graph is None:
        _graph = MemoryGraph()
    return _graph
