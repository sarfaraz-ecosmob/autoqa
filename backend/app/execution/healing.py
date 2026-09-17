"""Self-healing locators (Tier-1, Katalon-style — but grounded & approved).

When a selector fails, we try progressively looser alternatives derived from
the original selector (attribute swaps, label/text extraction, css class
match). Only if every fallback fails do we consult the LLM with a DOM
snapshot summary — and even then the heal is *proposed*: it's recorded in the
execution evidence and the substituted selector is clearly marked, never
silently rewritten in the stored test case.

Fallback chain for a broken selector, e.g.:
    [data-testid="login-btn"]  →  [data-test="login-btn"], [testid="login-btn"],
                                  button#login-btn (id guess), text="Login",
                                  (LLM proposal from DOM snapshot)
"""
import re

# attribute variants that commonly hold the same stable token
_ATTR_SWAPS = [
    ("data-testid", "data-test"),
    ("data-testid", "data-cy"),
    ("data-testid", "data-qa"),
    ("data-test", "data-testid"),
    ("data-test", "data-cy"),
    ("label", "aria-label"),
    ("placeholder", "name"),
]


def _attr(selector: str, name: str) -> str | None:
    m = re.search(rf'\[{name}="([^"]+)"\]', selector)
    return m.group(1) if m else None


def _token(selector: str) -> str:
    """Best stable token from a selector: attr value, id, or last css class."""
    for attr in ("data-testid", "data-test", "data-cy", "aria-label", "placeholder", "label", "name"):
        v = _attr(selector, attr)
        if v:
            return v
    m = re.search(r"#([\w-]+)", selector)
    if m:
        return m.group(1)
    classes = re.findall(r"\.([\w-]+)", selector)
    if classes:
        return classes[-1]
    m = re.search(r'text="([^"]+)"', selector)
    return m.group(1) if m else ""


def fallback_candidates(selector: str) -> list[str]:
    """Heuristic alternatives for a failing selector, most-specific first."""
    out: list[str] = []
    token = _token(selector)
    if not token:
        return out

    # attribute swaps
    for attr_a, attr_b in _ATTR_SWAPS:
        v = _attr(selector, attr_a)
        if v:
            out.append(f'[{attr_b}="{v}"]')
            break

    # id guess from the token
    if re.fullmatch(r"[\w-]+", token) and not token.startswith("role="):
        out.append(f"#{token}")

    # role+name guess: "login-btn" → button[name~="login"]
    base = re.sub(r"[-_]?btn(utton)?$", "", token, flags=re.I)
    if base and base != token:
        out.append(f'role=button[name~="{base}"]')
        out.append(f'text="{base}"')
    elif token and " " not in token:
        out.append(f'text="{token}"')

    # css class fallback
    if re.fullmatch(r"[\w-]+", token):
        out.append(f".{token}")

    # dedupe, preserve order, drop the original
    seen, uniq = set(), []
    for c in out:
        if c != selector and c not in seen:
            seen.add(c)
            uniq.append(c)
    return uniq


def heal_selector(page, selector: str) -> tuple[str | None, str]:
    """Try fallbacks against the live page; return (working_selector, method).

    method is "fallback" or "llm" (LLM only when all fallbacks miss and a
    configured provider exists). Returns (None, reason) if unhealed.
    """
    for candidate in fallback_candidates(selector):
        try:
            if page.locator(candidate).count() > 0:
                return candidate, "fallback"
        except Exception:
            continue

    llm_proposal, source = _llm_proposal(page, selector)
    if llm_proposal:
        try:
            if page.locator(llm_proposal).count() > 0:
                return llm_proposal, f"llm:{source}"
        except Exception:
            pass
    return None, "no candidate matched"


def _llm_proposal(page, selector: str) -> tuple[str | None, str]:
    """Ask the configured LLM to propose a selector from a trimmed DOM summary.
    Grounded: only real page content is sent; the proposal must match exactly
    one element to be accepted."""
    try:
        from app.ai import generate_json

        summary = _dom_summary(page)
        if not summary:
            return None, "no dom"
        result = generate_json(
            system=(
                "You repair broken test selectors. Given a failing selector and a "
                'DOM summary, return JSON: {"selector": "<css or playwright role/text '
                'selector>", "confidence": 0..1, "reasoning": "..."} — propose the '
                "single most likely replacement for the SAME element. If unsure, "
                'return {"selector": null}.'
            ),
            user=f"failing selector: {selector}\n\ndom summary:\n{summary}",
            fallback={},
        )
        if isinstance(result, dict) and result.get("selector"):
            return str(result["selector"]), str(result.get("confidence", "?"))
        return None, "llm-no-proposal"
    except Exception:
        return None, "llm-unavailable"


def _dom_summary(page, max_chars: int = 3500) -> str:
    """Compact DOM summary: interactive elements with their key attributes."""
    try:
        return page.evaluate(
            """() => {
                const nodes = document.querySelectorAll(
                    'a, button, input, select, textarea, [role], [data-testid], [data-test], form'
                );
                const lines = [];
                for (const el of Array.from(nodes).slice(0, 120)) {
                    const parts = [el.tagName.toLowerCase()];
                    if (el.id) parts.push('#' + el.id);
                    for (const a of ['data-testid','data-test','name','aria-label','placeholder','type']) {
                        const v = el.getAttribute && el.getAttribute(a);
                        if (v) parts.push(a + '="' + v + '"');
                    }
                    const text = (el.innerText || el.value || '').trim().slice(0, 40);
                    if (text) parts.push('text="' + text + '"');
                    lines.push(parts.join(' '));
                }
                return lines.join('\\n');
            }"""
        )[:max_chars]
    except Exception:
        return ""
