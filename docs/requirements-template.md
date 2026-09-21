# <Product / Feature Name> — Requirements

<!--
  HOW TO USE THIS TEMPLATE
  ------------------------
  1. Replace every section below with your own; keep ONE blank line between sections.
  2. Give each requirement an ID line like "REQ-<AREA>-001: Short title".
     - IDs must be unique within the project (REQ-AUTH-001, REQ-CART-002, ...).
     - If you omit IDs, the importer auto-assigns REQ-GEN-001, REQ-GEN-002, ...
  3. Put acceptance criteria in the body — the more concrete, the better the
     generated test cases.
  4. Mention an API as "METHOD /path" (e.g. POST /api/checkout) to make the
     generator produce API-type cases instead of browser-type.
  5. Mentions of email / OTP / reset link / verification code automatically add
     expired-link and link-reuse negative cases.
  6. PRIORITY & TAGS:
     - Markdown/text: add lines `priority: high` and `tags: auth, smoke`
       anywhere in the section (they are removed from the description).
     - CSV/XLSX: use the `priority` and `tags` columns (see table below).
     - JSON: use the `priority` and `tags`/`labels` keys; Jira exports also
       support `fields.labels` and `fields.priority.name`.
     - Priority accepts critical/high/medium/low plus shorthands
       P0/P1/P2/P3, blocker, urgent, trivial, normal, severity-style words;
       unknown or empty values fall back to medium.
     - Tags split on commas, semicolons, or pipes ("auth; smoke" → auth, smoke).
     - If omitted, priority = medium and tags = empty. Priority is inherited
       by every generated test case.
  7. Upload via: Project → Requirements tab → "Upload file…" (or paste the text).
     Re-importing the same IDs is safe — duplicates are skipped, not duplicated.
  8. Max lengths: title 300 chars, description 8000 chars.
-->

## REQ-AUTH-001: User can log in with username and password

As a registered user, I want to log in so that I can access my dashboard.

Acceptance criteria:
- Valid credentials redirect to the dashboard within 3 seconds.
- Wrong password shows "Invalid credentials" and never returns a 500.
- Submitting empty fields is blocked with a validation message.
- Password field masks input and supports paste.

priority: critical
tags: auth, smoke

## REQ-AUTH-002: Login API rejects malformed requests

POST /api/login accepts JSON {"username": "...", "password": "..."} and returns 200 with a token on success.

Acceptance criteria:
- Missing username or password returns 400.
- Invalid JSON returns 400, never 5xx.
- Injection payloads in fields are rejected or safely echoed.

priority: high
tags: auth, api, security

## REQ-CART-001: User can add a product to the cart

As a shopper, I want to add a product to the cart so that I can purchase it later.

Acceptance criteria:
- "Add to cart" increments the cart badge count.
- Quantity respects the configured max (10); exceeding it is rejected.
- Cart contents persist across page reloads for logged-in users.

priority: P1
tags: cart | ui

## REQ-CHECKOUT-001: Password reset via email link

As a user who forgot my password, I want an email reset link so that I can regain access.

Acceptance criteria:
- Requesting a reset for a registered email sends a link valid for 30 minutes.
- An expired reset link is rejected with a clear message.
- A reset link cannot be reused after it has been consumed.

priority: blocker
tags: reset, email

## REQ-REPORT-001: Dashboard shows order summary

As a logged-in user, I want an order summary on the dashboard so that I can track my purchases.

Acceptance criteria:
- Summary lists the 10 most recent orders with status and total.
- Currency amounts are formatted per the user's locale.
- Empty state shows "No orders yet" instead of a blank panel.

priority: medium
tags: reporting, dashboard

---

# CSV / XLSX variant

Use these headers (aliases work too: key/req_id, summary/name, details/body,
prio/severity, label/labels):

```csv
id,title,description,priority,tags
REQ-AUTH-001,User can log in,Valid credentials reach the dashboard; wrong password shows a handled error,critical,auth;smoke
REQ-CART-001,User can add to cart,Badge increments; max quantity 10 enforced,P1,cart|ui
REQ-CHECKOUT-001,Password reset via email,Link expires in 30 minutes; cannot be reused,blocker,reset;email
```

# JSON variant (flat)

```json
{
  "issues": [
    {
      "external_id": "REQ-AUTH-001",
      "title": "User can log in",
      "description": "Valid credentials reach the dashboard.",
      "priority": "critical",
      "tags": ["auth", "smoke"]
    }
  ]
}
```
