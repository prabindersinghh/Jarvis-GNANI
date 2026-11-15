"""JARVIS memory graph: local, private, redacted, deduped, graphify-exportable. Offline; no network."""
from __future__ import annotations

import json

from jarvis.graph.store import MemoryGraph


def _g(tmp_path):
    return MemoryGraph(path=tmp_path / "g.json")


def test_record_turn_links_date_session_app_tool_outcome(tmp_path):
    g = _g(tmp_path)
    g.record_turn("open chrome and search manufacturing news", session_id="s1", app="chrome.exe",
                  tools={"mcp__jarvis__computer_app": 1, "computer_observe": 2}, verified=True)
    types = g.stats()["by_type"]
    for t in ("date", "session", "turn", "app", "tool", "outcome"):
        assert types.get(t, 0) >= 1, t
    # the tool node dropped the mcp prefix
    assert any(n["type"] == "tool" and n["key"] == "computer_app" for n in g.nodes.values())


def test_dedup_and_counts(tmp_path):
    g = _g(tmp_path)
    for _ in range(3):
        g.add_node("app", "chrome.exe", label="Chrome")
    nid = g._nid("app", "chrome.exe")
    assert g.nodes[nid]["count"] == 3
    assert len([n for n in g.nodes.values() if n["type"] == "app"]) == 1


def test_structured_secrets_are_redacted_not_stored(tmp_path):
    # the graph is local-only, but structured secrets (cards, keys, tokens) are still redacted before storage
    g = _g(tmp_path)
    g.record_turn("set api_key=sk-abcdefghijklmnopqrstuvwx and pay card 4111 1111 1111 1111", session_id="s1")
    g.record_memory("token=ghp_ABCDEFGHIJKLMNOPQRSTUV for the repo")
    blob = json.dumps({"nodes": g.nodes, "edges": g.edges})
    assert "4111 1111 1111 1111" not in blob
    assert "sk-abcdefghijklmnopqrstuvwx" not in blob
    assert "ghp_ABCDEFGHIJKLMNOPQRSTUV" not in blob


def test_recall_finds_topic(tmp_path):
    g = _g(tmp_path)
    g.record_turn("search for manufacturing automation news", session_id="s1")
    hits = g.recall("manufacturing")
    assert hits and any("manufactur" in (h.get("label", "") + h["key"]).lower() for h in hits)


def test_persistence_and_corrupt_recovery(tmp_path):
    p = tmp_path / "g.json"
    g = MemoryGraph(path=p)
    g.record_turn("open notepad", session_id="s1", app="notepad.exe")
    g.save()
    assert MemoryGraph(path=p).stats()["nodes"] == g.stats()["nodes"]   # survives reload
    p.write_text("{ this is : not json", encoding="utf-8")               # corrupt file
    assert MemoryGraph(path=p).stats()["nodes"] == 0                     # recovers to empty, no crash


def test_bounded(tmp_path):
    g = MemoryGraph(path=tmp_path / "g.json", max_nodes=20, max_edges=40)
    for i in range(200):
        g.add_node("topic", f"t{i}")
    assert len(g.nodes) <= 20


def test_graphify_export_shape(tmp_path):
    g = _g(tmp_path)
    g.record_turn("open chrome", session_id="s1", app="chrome.exe", tools={"computer_app": 1}, verified=True)
    out = g.export_graphify(tmp_path / "graph.json")
    d = json.loads(out.read_text(encoding="utf-8"))
    assert {"nodes", "edges", "meta"} <= set(d)
    assert all({"id", "type", "label"} <= set(n) for n in d["nodes"])
    assert all({"source", "target", "type"} <= set(e) for e in d["edges"])
    assert d["meta"]["source"] == "jarvis-memory-graph"
