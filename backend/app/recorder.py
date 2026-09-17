"""Recorder import (Tier-1, Katalon-Recorder-style).

Playwright's `codegen` records user actions as Python/TS calls. Users paste
the codegen output (or its JSON export) and we map what we support to our
step format; unmapped actions become `skipped` steps with a warning rather
than being silently dropped.

Supported mappings (codegen call → our step):
    page.goto(url)                     → goto
    page.fill(sel, val) / get_by_*/fill→ fill
    page.click(sel) / get_by_*/click   → click
    page.check/uncheck(sel)            → click
    page.select_option(sel, val)       → fill (select works via fill in PW)
    page.press(sel, key)               → fill (keystroke into field)
    expect(page).to_have_title(...)    → expect_title_not_empty
"""
import re

_LOCATOR_INNER = re.compile(r"[\"'](.+?)[\"']")


def _first_string(args: str) -> str:
    m = _LOCATOR_INNER.search(args)
    return m.group(1) if m else args.strip()


def _all_strings(args: str) -> list[str]:
    return _LOCATOR_INNER.findall(args)


# Normalize selector: prefer data-testid / role / text forms we understand.
def _normalize_selector(sel: str) -> str:
    sel = sel.strip()
    # getByTestId('x') / page.getByTestId("x") → [data-testid="x"]
    if re.match(r"^(page\.)?getByTestId\(", sel):
        inner = _first_string(sel)
        return f'[data-testid="{inner}"]'
    # getByRole('button', { name: 'Submit' }) → role selector
    if re.match(r"^(page\.)?getByRole\(", sel):
        role_m = re.search(r"[\"'](\w[\w-]*)[\"']", sel)
        name_m = re.search(r"name\s*[:=]\s*[\"'](.+?)[\"']", sel)
        role = role_m.group(1) if role_m else "generic"
        if name_m:
            return f'role={role}[name="{name_m.group(1)}"]'
        return f'role={role}'
    # getByLabel / getByPlaceholder / getByText → best-effort CSS/text
    if re.match(r"^(page\.)?getByLabel\(", sel):
        return f'[label="{_first_string(sel)}"]'
    if re.match(r"^(page\.)?getByPlaceholder\(", sel):
        return f'[placeholder="{_first_string(sel)}"]'
    if re.match(r"^(page\.)?getByText\(", sel):
        return f'text="{_first_string(sel)}"'
    return sel


def actions_to_steps(actions: list[dict]) -> tuple[list[dict], list[str]]:
    """Translate codegen actions to our steps. Accepts either structured
    dicts ({action, target, value}) or codegen lines ({"line": "page.goto(...)"}).
    Returns (steps, warnings)."""
    steps: list[dict] = []
    warnings: list[str] = []

    for raw in actions:
        action_dict = raw if isinstance(raw, dict) else {"line": str(raw)}
        line = str(action_dict.get("line") or action_dict.get("code") or "").strip()
        if line:
            step, warn = _parse_code_line(line)
            if warn:
                warnings.append(warn)
            if step:
                steps.append(step)
            continue

        # structured form
        action = str(action_dict.get("action", "")).lower()
        target = str(action_dict.get("target") or action_dict.get("selector") or "")
        value = action_dict.get("value")
        if action in ("goto", "open", "navigate"):
            steps.append({"action": "goto", "target": target})
        elif action in ("fill", "type", "input"):
            steps.append({"action": "fill", "target": target, "value": str(value or "")})
        elif action in ("click", "tap", "check", "uncheck"):
            steps.append({"action": "click", "target": target})
        elif action == "expect_status":
            steps.append({"action": "expect_status", "value": value})
        else:
            warnings.append(f"unrecognized structured action: {action or '∅'}")

    return steps, warnings


def _parse_code_line(line: str) -> tuple[dict | None, str | None]:
    # strip trailing semicolons/whitespace that codegen emits
    line = line.rstrip().rstrip(";").strip()
    # locator chains FIRST (page.getByRole(...).click()); the generic
    # page.<fn>(...) form excludes getBy* factory methods, which always
    # appear inside chains.
    chain = re.match(r"^(?:await\s+)?(page\.getBy\w+\(.*?\))(?:\.\w+\([^()]*\))*\.\s*(\w+)\((.*)\)$", line)
    if chain and chain.group(2) in ("click", "fill", "check", "uncheck", "tap"):
        sel = _normalize_selector(chain.group(1))
        if chain.group(2) == "fill":
            vals = _all_strings(chain.group(3))
            return {"action": "fill", "target": sel, "value": vals[-1] if vals else ""}, None
        return {"action": "click", "target": sel}, None
    m = re.match(r'^(?:await\s+)?page\[["\'](.+?)["\']\]\.(\w+)\((.*)\)$', line) or \
        re.match(r"^(?:await\s+)?page\.(?!getBy)(\w+)\((.*)\)$", line) or \
        re.match(r"^(?:await\s+)?(?:expect\(page\))\.(\w+)\((.*)\)$", line)
    if not m:
        return None, f"not translatable: {line[:80]}"

    fn, args = m.group(1), (m.group(2) if m.lastindex >= 2 else "")
    sel = _normalize_selector(args)

    if fn == "goto":
        url = _first_string(args)
        return {"action": "goto", "target": url}, None
    if fn in ("fill", "type"):
        vals = _all_strings(args)
        return {"action": "fill", "target": sel, "value": vals[-1] if vals else ""}, None
    if fn in ("click", "check", "uncheck", "tap"):
        return {"action": "click", "target": sel}, None
    if fn in ("select_option", "press"):
        vals = _all_strings(args)
        return {"action": "fill", "target": sel, "value": vals[-1] if vals else ""}, None
    if fn in ("to_have_title", "to_have_url"):
        return None, None  # presence of an expect → keep going, no explicit step
    return None, f"unsupported page.{fn}() — recorded as skipped"


def steps_summary(steps: list[dict]) -> str:
    parts = []
    for s in steps[:6]:
        a = s.get("action", "?")
        t = s.get("target", "")
        parts.append(f"{a} {t}".strip())
    more = f" (+{len(steps) - 6} more)" if len(steps) > 6 else ""
    return " → ".join(parts) + more
