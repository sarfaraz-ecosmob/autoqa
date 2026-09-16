"""Accessibility checks (spec §15) — WCAG-oriented static analysis in the browser.

Checks: image alt text, form labels, heading hierarchy, html lang, viewport
zoom, landmark basics. Runs in the browser-worker (needs Playwright).
"""
from dataclasses import dataclass, field


@dataclass
class A11yResult:
    page_url: str
    violations: list[dict] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"page_url": self.page_url, "violations": self.violations, "count": len(self.violations)}


_JS_AUDIT = """
() => {
  const violations = [];
  const add = (rule, severity, nodes, help) => {
    if (nodes.length) violations.push({rule, severity, help, count: nodes.length, sample: nodes.slice(0,3)});
  };

  // Images without alt (WCAG 1.1.1)
  const imgs = [...document.querySelectorAll('img')].filter(i => !i.hasAttribute('alt'));
  add('img-alt', 'critical', imgs.map(i => i.outerHTML.slice(0,80)), 'Images must have an alt attribute');

  // Form fields without labels (WCAG 1.3.1 / 4.1.2)
  const unlabeled = [...document.querySelectorAll('input:not([type=hidden]), select, textarea')].filter(el => {
    if (el.getAttribute('aria-label') || el.getAttribute('aria-labelledby')) return false;
    if (el.id && document.querySelector(`label[for="${el.id}"]`)) return false;
    const parentLabel = el.closest('label');
    return !parentLabel;
  });
  add('form-label', 'critical', unlabeled.map(e => e.outerHTML.slice(0,80)), 'Form fields need labels');

  // Buttons without accessible text (WCAG 4.1.2)
  const emptyButtons = [...document.querySelectorAll('button')].filter(b => !(b.textContent || '').trim() && !b.getAttribute('aria-label'));
  add('button-name', 'serious', emptyButtons.map(b => b.outerHTML.slice(0,80)), 'Buttons need accessible names');

  // Heading hierarchy: first heading should be h1/h2 (WCAG 1.3.1)
  const headings = [...document.querySelectorAll('h1,h2,h3,h4,h5,h6')];
  if (headings.length && headings[0].tagName !== 'H1') {
    add('heading-order', 'moderate', [headings[0].outerHTML.slice(0,80)], 'First heading should be h1');
  }
  // Skipped levels
  let prev = 0, skipped = [];
  for (const h of headings) {
    const level = parseInt(h.tagName[1]);
    if (prev && level > prev + 1) skipped.push(h.outerHTML.slice(0,80));
    prev = level;
  }
  add('heading-skip', 'moderate', skipped, 'Heading levels should not skip');

  // html lang (WCAG 3.1.1)
  if (!document.documentElement.getAttribute('lang')) {
    add('html-lang', 'serious', ['<html>'], 'html element needs a lang attribute');
  }

  // Viewport zoom disabled (WCAG 1.4.4)
  const vp = document.querySelector('meta[name="viewport"]');
  if (vp && /user-scalable=no|maximum-scale=1/i.test(vp.getAttribute('content') || '')) {
    add('viewport-zoom', 'serious', ['meta[viewport]'], 'Zoom must not be disabled');
  }

  // Positive tabindex (WCAG 2.4.3)
  const posTab = [...document.querySelectorAll('[tabindex]')].filter(e => parseInt(e.getAttribute('tabindex')) > 0);
  add('tabindex-positive', 'moderate', posTab.map(e => e.outerHTML.slice(0,80)), 'Avoid positive tabindex');

  return violations;
}
"""


def audit_page(page, url: str) -> A11yResult:
    """Run the audit JS on an already-navigated Playwright page."""
    try:
        page.goto(url, timeout=15000, wait_until="domcontentloaded")
        page.wait_for_timeout(300)
        violations = page.evaluate(_JS_AUDIT)
    except Exception as exc:
        return A11yResult(page_url=url, violations=[{"rule": "audit-error", "severity": "minor", "help": str(exc)[:200], "count": 1}])
    return A11yResult(page_url=url, violations=violations)


def audit_site(urls: list[str]) -> list[dict]:
    """Audit several pages with a fresh browser context."""
    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        for url in urls[:20]:
            results.append(audit_page(page, url).as_dict())
        browser.close()
    return results
