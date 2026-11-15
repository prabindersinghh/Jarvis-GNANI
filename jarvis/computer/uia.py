"""Windows UI Automation element model for JARVIS.

Produces the compact state the TRD §6 describes:

    {"id": 42, "role": "button", "name": "Download", "automation_id": "...",
     "enabled": true, "visible": true, "bounds": [l, t, r, b], "value": "...",
     "patterns": ["Invoke"]}

and performs element actions (invoke / select / toggle / expand / collapse /
set_value / focus / click-at-center). Elements are re-resolved from a stored
observation by *runtime id → (role, name, bounds)* so nothing dangles across calls.

Only interactable/informative controls are listed (buttons, edits, list items,
menu items, tabs, links, checkboxes, combo boxes, tree items, named text...).
Unnamed panes/groups are walked but not listed.
"""
from __future__ import annotations

import logging
import time

from .engine import input as winput
from .engine import winfind

log = logging.getLogger("jarvis.uia")

ROLE_MAP = {
    "ButtonControl": "button", "CheckBoxControl": "checkbox", "RadioButtonControl": "radio",
    "ComboBoxControl": "combobox", "EditControl": "edit", "HyperlinkControl": "link",
    "ListItemControl": "listitem", "MenuItemControl": "menuitem", "TabItemControl": "tab",
    "TreeItemControl": "treeitem", "SplitButtonControl": "button", "SliderControl": "slider",
    "SpinnerControl": "spinner", "DataItemControl": "dataitem", "HeaderItemControl": "header",
    "ImageControl": "image", "TextControl": "text", "DocumentControl": "document",
    "ListControl": "list", "MenuControl": "menu", "TabControl": "tabs", "TreeControl": "tree",
    "ToolBarControl": "toolbar", "StatusBarControl": "statusbar", "ScrollBarControl": "scrollbar",
    "TitleBarControl": "titlebar", "WindowControl": "window", "PaneControl": "pane",
    "GroupControl": "group", "TableControl": "table", "ProgressBarControl": "progress",
    "ToolTipControl": "tooltip", "CustomControl": "custom", "MenuBarControl": "menubar",
}
RESOLVE_GRACE_S = 1.5  # bounded wait for a just-created window to become enumerable
VALUE_ROLES = {"edit", "combobox", "document", "slider", "spinner"}
CONTAINER_ROLES = {"pane", "group", "window", "custom", "list", "tree", "table", "toolbar", "menubar",
                   "tabs", "menu", "statusbar", "titlebar", "scrollbar", "document"}
NOISE_ROLES = {"scrollbar", "thumb", "progress", "separator"}
NOISE_AUTOMATION_IDS = {"UpButton", "DownButton", "UpPageButton", "DownPageButton", "LeftButton", "RightButton",
                        "VerticalSmallDecrease", "VerticalSmallIncrease", "VerticalLargeDecrease", "VerticalLargeIncrease",
                        "HorizontalSmallDecrease", "HorizontalSmallIncrease", "HorizontalLargeDecrease", "HorizontalLargeIncrease",
                        "VerticalScrollBar", "HorizontalScrollBar", "NonClientVerticalScrollBar", "NonClientHorizontalScrollBar"}
CLICKABLE_ROLES = {"button", "link", "menuitem", "tab", "listitem", "treeitem", "checkbox", "radio",
                   "combobox", "edit", "dataitem", "header", "image", "slider", "spinner"}


def _auto():
    import uiautomation as auto
    return auto


def _bounds(ctrl) -> list[int] | None:
    try:
        r = ctrl.BoundingRectangle
        if r.right <= r.left or r.bottom <= r.top:
            return None
        return [int(r.left), int(r.top), int(r.right), int(r.bottom)]
    except Exception:
        return None


def _patterns(ctrl) -> list[str]:
    auto = _auto()
    out = []
    for pid, label in ((auto.PatternId.InvokePattern, "Invoke"), (auto.PatternId.ValuePattern, "Value"),
                       (auto.PatternId.SelectionItemPattern, "SelectionItem"), (auto.PatternId.TogglePattern, "Toggle"),
                       (auto.PatternId.ExpandCollapsePattern, "ExpandCollapse"), (auto.PatternId.ScrollPattern, "Scroll"),
                       (auto.PatternId.TextPattern, "Text"), (auto.PatternId.RangeValuePattern, "RangeValue")):
        try:
            if ctrl.GetPattern(pid) is not None:
                out.append(label)
        except Exception:
            pass
    return out


def _value(ctrl, patterns: list[str]) -> str | None:
    try:
        if "Value" in patterns:
            v = ctrl.GetValuePattern().Value
            return (v or "")[:200]
        if "Toggle" in patterns:
            return {0: "off", 1: "on", 2: "mixed"}.get(int(ctrl.GetTogglePattern().ToggleState), "?")
        if "ExpandCollapse" in patterns:
            return {0: "collapsed", 1: "expanded", 2: "partial", 3: "leaf"}.get(int(ctrl.GetExpandCollapsePattern().ExpandCollapseState), "?")
        if "SelectionItem" in patterns:
            return "selected" if ctrl.GetSelectionItemPattern().IsSelected else "unselected"
        if "RangeValue" in patterns:
            return str(ctrl.GetRangeValuePattern().Value)
    except Exception:
        return None
    return None


def resolve_window(query: str | int | None) -> tuple[object, dict]:
    """(uia_control, window_dict) for a query: None/'foreground', hwnd, or title/process substring."""
    auto = _auto()
    if query in (None, "", "foreground", "active"):
        hwnd = winfind.foreground_window()
        wins = [w for w in winfind.list_windows() if w["hwnd"] == hwnd]
        w = wins[0] if wins else {"hwnd": hwnd, "title": "", "process": "", "rect": None}
    elif isinstance(query, int) or (isinstance(query, str) and query.isdigit()):
        hwnd = int(query)
        wins = [w for w in winfind.list_windows() if w["hwnd"] == hwnd]
        w = wins[0] if wins else {"hwnd": hwnd, "title": "", "process": "", "rect": None}
    else:
        matches = winfind.find_windows(query)
        end = time.perf_counter() + RESOLVE_GRACE_S
        while not matches and time.perf_counter() < end:  # windows just created may not be visible for a few ms
            time.sleep(0.05)
            matches = winfind.find_windows(query)
        if not matches:
            raise LookupError(f"no window matching {query!r}")
        w = matches[0]
    return auto.ControlFromHandle(w["hwnd"]), w


def snapshot(query: str | int | None = None, max_depth: int = 14, max_elements: int = 120,
             include_containers: bool = False, interactive_only: bool = False) -> tuple[dict, list[dict], dict]:
    """Walk the window's UIA tree → (window, elements, meta). Never raises for a per-node failure."""
    t0 = time.perf_counter()
    root, window = resolve_window(query)
    auto = _auto()
    elements: list[dict] = []
    walked = 0
    budget = max_elements * 25
    for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
        walked += 1
        if walked > budget or len(elements) >= max_elements:
            break
        try:
            tname = ctrl.ControlTypeName
            role = ROLE_MAP.get(tname, tname.replace("Control", "").lower())
            try:
                if ctrl.IsOffscreen:
                    continue
            except Exception:
                pass
            b = _bounds(ctrl)
            if b is None:
                continue
            name = (ctrl.Name or "").strip()[:120]
            if role in NOISE_ROLES and not include_containers:
                continue
            if role in CONTAINER_ROLES and not name and not include_containers:
                continue
            try:
                aid = (ctrl.AutomationId or "").strip()
            except Exception:
                aid = ""
            if aid in NOISE_AUTOMATION_IDS and not include_containers:
                continue
            patterns = _patterns(ctrl)
            if interactive_only and not (patterns or role in CLICKABLE_ROLES):
                continue
            if not name and role not in VALUE_ROLES and not patterns:
                continue
            try:
                enabled = bool(ctrl.IsEnabled)
            except Exception:
                enabled = True
            el = {"id": len(elements) + 1, "role": role, "name": name, "enabled": enabled, "visible": True, "bounds": b}
            if aid:
                el["automation_id"] = aid[:80]
            if patterns:
                el["patterns"] = patterns
            v = _value(ctrl, patterns)
            if v not in (None, ""):
                el["value"] = v
            try:
                el["rid"] = list(ctrl.GetRuntimeId())
            except Exception:
                pass
            elements.append(el)
        except Exception as e:  # noqa: BLE001
            log.debug("walk error: %s", e)
    meta = {"uia_ms": round((time.perf_counter() - t0) * 1000), "nodes_walked": walked,
            "truncated": len(elements) >= max_elements}
    return window, elements, meta


def find_control(window_hwnd: int, el: dict, max_depth: int = 14):
    """Re-resolve a stored element dict to a live control (runtime id first, then role+name+bounds)."""
    auto = _auto()
    root = auto.ControlFromHandle(window_hwnd)
    rid = el.get("rid")
    fallback = None
    for ctrl, _d in auto.WalkControl(root, includeTop=False, maxDepth=max_depth):
        try:
            if rid and list(ctrl.GetRuntimeId()) == rid:
                return ctrl
            if fallback is None:
                tname = ctrl.ControlTypeName
                role = ROLE_MAP.get(tname, tname.replace("Control", "").lower())
                if role == el.get("role") and (ctrl.Name or "").strip()[:120] == el.get("name") and _bounds(ctrl) == el.get("bounds"):
                    fallback = ctrl
        except Exception:
            continue
    return fallback


def center(el: dict) -> tuple[int, int]:
    left, top, right, bottom = el["bounds"]
    return (left + right) // 2, (top + bottom) // 2


CLICK_FIRST_ROLES = {"menuitem", "button", "tab", "listitem", "link", "treeitem", "checkbox", "radio", "dataitem", "image"}


def _visible_on_screen(el: dict, foreground_hwnd: int | None) -> bool:
    """True when the element's window is the foreground window and its center is on a monitor."""
    if foreground_hwnd is None:
        return False
    try:
        if winfind.foreground_window() != foreground_hwnd:
            return False
        from .engine import displays
        x, y = center(el)
        for m in displays.list_monitors():
            if m["left"] <= x < m["left"] + m["width"] and m["top"] <= y < m["top"] + m["height"]:
                return True
    except Exception:  # noqa: BLE001
        return False
    return False


def act_on(ctrl, el: dict, action: str = "auto", value: str | None = None, foreground_hwnd: int | None = None) -> dict:
    """Perform an action on a live control via UIA patterns, falling back to a physical click."""
    patterns = set(el.get("patterns") or _patterns(ctrl))
    role = el.get("role")
    try:
        if action == "set_value":
            if "Value" in patterns:
                ctrl.GetValuePattern().SetValue(value or "")
                return {"method": "ValuePattern.SetValue", "ok": True}
            ctrl.SetFocus()
            winput.press("ctrl+a")
            winput.type_text(value or "", chunk=1, interval=0.004)
            return {"method": "focus+type", "ok": True}
        if action == "focus":
            ctrl.SetFocus()
            return {"method": "SetFocus", "ok": True}
        if action in ("expand", "collapse"):
            p = ctrl.GetExpandCollapsePattern()
            (p.Expand if action == "expand" else p.Collapse)()
            return {"method": f"ExpandCollapse.{action}", "ok": True}
        if action == "select" or (action == "auto" and "SelectionItem" in patterns and role in ("listitem", "tab", "radio", "treeitem", "dataitem")):
            ctrl.GetSelectionItemPattern().Select()
            return {"method": "SelectionItem.Select", "ok": True}
        if action == "toggle" or (action == "auto" and "Toggle" in patterns and role == "checkbox"):
            ctrl.GetTogglePattern().Toggle()
            return {"method": "Toggle", "ok": True}
        if action == "auto" and role in CLICK_FIRST_ROLES and _visible_on_screen(el, foreground_hwnd):
            # Physical click on a visible control in the foreground window: ~50 ms vs ~500 ms for a synchronous
            # InvokePattern round-trip in WinUI apps. Invoke stays the path for explicit action="invoke" or when the
            # element's window is not in front (e.g. background automation).
            x, y = center(el)
            winput.click("left", 1, x, y)
            return {"method": "click@center", "ok": True, "point": [x, y]}
        if action in ("invoke", "auto") and "Invoke" in patterns:
            ctrl.GetInvokePattern().Invoke()
            return {"method": "Invoke", "ok": True}
        if action in ("invoke", "auto") and "ExpandCollapse" in patterns:
            ctrl.GetExpandCollapsePattern().Expand()
            return {"method": "ExpandCollapse.Expand", "ok": True}
    except Exception as e:  # noqa: BLE001
        log.info("pattern action %s failed (%s); falling back to click", action, e)
    x, y = center(el)
    try:
        ctrl.SetFocus()
    except Exception:
        pass
    winput.click("left", 1, x, y)
    return {"method": "click@center", "ok": True, "point": [x, y]}


def read_text(ctrl, max_chars: int = 20000) -> str:
    """Best-effort text of a control: TextPattern → ValuePattern → Name."""
    for getter in (lambda: ctrl.GetTextPattern().DocumentRange.GetText(max_chars),
                   lambda: ctrl.GetValuePattern().Value,
                   lambda: ctrl.Name):
        try:
            v = getter()
            if v:
                return str(v)[:max_chars]
        except Exception:
            continue
    return ""


def find_elements(elements: list[dict], name: str | None = None, role: str | None = None,
                  automation_id: str | None = None, exact: bool = False, enabled_only: bool = True) -> list[dict]:
    """Filter a stored element list. Name match is case-insensitive substring (or exact)."""
    out = []
    q = (name or "").strip().lower()
    for e in elements:
        if enabled_only and not e.get("enabled", True):
            continue
        if role and e.get("role") != role:
            continue
        if automation_id and e.get("automation_id") != automation_id:
            continue
        if q:
            n = (e.get("name") or "").lower()
            if exact and n != q:
                continue
            if not exact and q not in n:
                continue
        out.append(e)
    out.sort(key=lambda e: (0 if (e.get("name") or "").lower() == q else 1, len(e.get("name") or "")))
    return out
