"""Demo application — AutoQA's default test target (spec §30).

A small "Demo Shop" with a polished, modern look — but deliberately flawed.
Every flaw below is a fixture for AutoQA's pipeline; do NOT "fix" them:

  - Crawler (Phase 2/3): multi-page site, forms, buttons, tables, modal,
    file upload, robots.txt, sitemap.xml, SPA-style client-side fetches
  - Auth flows: login form with the selectors the platform expects
    (input[name='username'], input[type='password'], button[type='submit']);
    a successful form login redirects to /dashboard so the standard
    `url_not_contains:login` success assertion can pass
  - Test cases (Phase 5/6): broken checkout endpoint (HTTP 500),
    graceful-400 paths, payment scenarios
  - Evidence (Phase 8): JS console error on /broken and on failed checkout
  - Security (Phase 10): raw input reflection on /search; NO security
    headers anywhere (no CSP/HSTS/nosniff/frame-options/referrer-policy)
  - Accessibility (Phase 11): no <html lang> attribute; form inputs have
    no labels/aria-labels (styled with placeholders only)

Contract kept for the platform's tests (routes and payload shapes):
  GET  /health                      -> {"status": "ok"}
  POST /login                       -> 200 {"ok": true, "token": "demo-token"}
                                        | 400 missing credentials
                                        | 500 when password == "wrong" (flaw)
  GET  /api/users                   -> {"users": [{"id": 1, "name": "alice"}, ...]}
  GET  /api/products                 -> {"products": [{"id": 1, "name": "Widget"}]}
  GET  /api/orders/{int}             -> {"id": N, "status": "shipped"}
  POST /api/checkout                -> always 500 (flaw)
"""
import base64
import hmac
import struct
import time

from fastapi import FastAPI, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response

app = FastAPI(title="Demo Shop", docs_url="/api-docs")

# ---------------------------------------------------------------------------
# Look & feel (inline, zero dependencies)
# ---------------------------------------------------------------------------
CSS = """
:root{
  --brand:#4f46e5; --brand-2:#7c3aed; --brand-soft:#eef2ff;
  --ink:#0f172a; --muted:#64748b; --line:#e2e8f0; --card:#ffffff;
  --bg:#f1f5f9; --ok:#059669; --warn:#d97706; --bad:#dc2626;
  --radius:16px; --shadow:0 1px 2px rgba(15,23,42,.06),0 8px 24px rgba(15,23,42,.06);
}
*{box-sizing:border-box}
body{
  margin:0; color:var(--ink); background:var(--bg);
  font:15px/1.6 ui-sans-serif,system-ui,-apple-system,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;
}
a{color:var(--brand); text-decoration:none}
a:hover{text-decoration:underline}
.topnav{
  position:sticky; top:0; z-index:10; display:flex; align-items:center; gap:18px;
  padding:12px 28px; background:rgba(255,255,255,.85); backdrop-filter:blur(8px);
  border-bottom:1px solid var(--line);
}
.brand{display:flex; align-items:center; gap:10px; font-weight:800; font-size:17px; letter-spacing:-.01em}
.brand .logo{
  width:30px;height:30px;border-radius:9px;display:grid;place-items:center;color:#fff;font-size:15px;
  background:linear-gradient(135deg,var(--brand),var(--brand-2));
}
.topnav .links{display:flex; gap:4px; flex-wrap:wrap}
.topnav .links a{padding:6px 12px; border-radius:9px; color:var(--muted); font-weight:600; font-size:14px}
.topnav .links a:hover{background:var(--brand-soft); color:var(--brand); text-decoration:none}
.pill{
  margin-left:auto; font-size:11px; font-weight:700; letter-spacing:.06em; text-transform:uppercase;
  color:var(--brand); background:var(--brand-soft); border:1px solid #e0e7ff;
  padding:4px 10px; border-radius:999px; white-space:nowrap;
}
.container{max-width:1020px; margin:28px auto 56px; padding:0 24px}
.hero{
  border-radius:22px; padding:44px 40px; color:#fff; position:relative; overflow:hidden;
  background:linear-gradient(120deg,#4338ca,#6d28d9 55%,#9333ea); box-shadow:var(--shadow);
}
.hero h1{margin:0 0 8px; font-size:34px; letter-spacing:-.02em}
.hero p{margin:0 0 22px; color:#e0e7ff; max-width:52ch}
.hero .cta{display:flex; gap:10px; flex-wrap:wrap}
.grid{display:grid; gap:16px}
.grid.cards{grid-template-columns:repeat(auto-fill,minmax(230px,1fr)); margin-top:22px}
.card{
  background:var(--card); border:1px solid var(--line); border-radius:var(--radius);
  box-shadow:var(--shadow); padding:22px;
}
.card h2{margin:0 0 6px; font-size:16px}
.card h3{margin:0 0 4px; font-size:15px}
.card p{margin:0; color:var(--muted); font-size:14px}
.card .emoji{font-size:26px; margin-bottom:10px; display:block}
.stat .num{font-size:28px; font-weight:800; letter-spacing:-.02em}
section.block{margin-top:34px}
section.block>h1{margin:0 0 4px; font-size:24px; letter-spacing:-.02em}
section.block>.sub{margin:0 0 18px; color:var(--muted)}
button,.btn{
  display:inline-flex; align-items:center; gap:8px; border:0; cursor:pointer;
  background:linear-gradient(135deg,var(--brand),var(--brand-2)); color:#fff;
  font-weight:700; font-size:14px; padding:10px 18px; border-radius:11px;
  box-shadow:0 1px 2px rgba(79,70,229,.35); transition:filter .15s ease, transform .05s ease;
}
button:hover,.btn:hover{filter:brightness(1.08); text-decoration:none}
button:active{transform:translateY(1px)}
button.ghost,.btn.ghost{
  background:#fff; color:var(--ink); border:1px solid var(--line); box-shadow:none;
}
input,select{
  font:inherit; color:var(--ink); background:#fff; border:1px solid var(--line);
  border-radius:11px; padding:10px 12px; width:100%; transition:border .15s ease, box-shadow .15s ease;
}
input:focus,select:focus{outline:none; border-color:var(--brand); box-shadow:0 0 0 3px rgba(79,70,229,.15)}
form.stack{display:grid; gap:12px; max-width:420px}
.searchbar{display:flex; gap:10px; max-width:520px; margin-top:18px}
.searchbar input{flex:1}
.alert{
  display:none; margin-top:14px; padding:12px 14px; border-radius:11px; font-size:14px;
  border:1px solid #fecaca; background:#fef2f2; color:#991b1b;
}
.alert.ok{border-color:#a7f3d0; background:#ecfdf5; color:#065f46}
.alert.show{display:block}
.chip{
  display:inline-block; padding:3px 10px; border-radius:999px; font-size:12px; font-weight:700;
  background:var(--brand-soft); color:var(--brand);
}
.chip.green{background:#ecfdf5; color:var(--ok)}
.chip.amber{background:#fffbeb; color:var(--warn)}
.chip.red{background:#fef2f2; color:var(--bad)}
.chip.grey{background:#f1f5f9; color:var(--muted)}
.flaw{
  display:inline-block; margin-top:12px; padding:5px 11px; border-radius:999px;
  font-size:12px; font-weight:600; background:#fffbeb; color:#92400e; border:1px dashed #fcd34d;
}
table{width:100%; border-collapse:collapse; background:#fff; border-radius:var(--radius); overflow:hidden;
  border:1px solid var(--line); box-shadow:var(--shadow)}
th{background:#f8fafc; text-align:left; font-size:12px; text-transform:uppercase; letter-spacing:.06em;
  color:var(--muted); padding:12px 16px; border-bottom:1px solid var(--line)}
td{padding:12px 16px; border-bottom:1px solid #f1f5f9; font-size:14px}
tr:last-child td{border-bottom:0}
.price{font-weight:800; color:var(--ink)}
.muted{color:var(--muted)}
.modal{
  position:fixed; inset:0; display:none; place-items:center; background:rgba(15,23,42,.45);
}
.modal[hidden]{display:none}
footer{
  border-top:1px solid var(--line); padding:22px 28px; text-align:center;
  color:var(--muted); font-size:13px; background:#fff;
}
@media (max-width:720px){
  .topnav{padding:10px 16px; flex-wrap:wrap}
  .pill{margin-left:0}
  .container{margin:18px auto 40px}
  .hero{padding:30px 24px}
  .hero h1{font-size:26px}
}
"""

_NAV = """
<nav class="topnav">
  <span class="brand"><span class="logo">DS</span>Demo Shop</span>
  <div class="links">
    <a href="/">Home</a>
    <a href="/login">Login</a>
    <a href="/dashboard">Dashboard</a>
    <a href="/users">Users</a>
    <a href="/products">Products</a>
    <a href="/orders">Orders</a>
    <a href="/upload">Upload</a>
    <a href="/broken">Broken</a>
  </div>
  <span class="pill">AutoQA demo target</span>
</nav>
"""

# NOTE: the missing lang="" attribute is a DELIBERATE accessibility flaw (Phase 11).
_SHELL = """<!doctype html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Demo Shop — __TITLE__</title>
<style>__CSS__</style>
</head>
<body>
__NAV__
<main class="container">
__BODY__
</main>
<footer>Demo Shop — a deliberately flawed test target for <strong>AutoQA</strong>. Every flaw is labeled and intentional.</footer>
</body>
</html>"""


def page(title: str, body: str) -> HTMLResponse:
    html = (
        _SHELL.replace("__TITLE__", title)
        .replace("__CSS__", CSS)
        .replace("__NAV__", _NAV)
        .replace("__BODY__", body)
    )
    return HTMLResponse(html)


# ---------------------------------------------------------------------------
# Infrastructure endpoints
# ---------------------------------------------------------------------------
@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/robots.txt", response_class=PlainTextResponse)
def robots() -> str:
    return "User-agent: *\nDisallow: /api-docs\n"


@app.get("/sitemap.xml", response_class=Response)
def sitemap() -> Response:
    urls = ["/", "/login", "/dashboard", "/users", "/products", "/orders", "/upload", "/broken"]
    entries = "".join(f"<url><loc>http://localhost:9000{u}</loc></url>" for u in urls)
    xml = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">' + entries + "</urlset>"
    )
    return Response(content=xml, media_type="application/xml")


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@app.get("/", response_class=HTMLResponse)
def home():
    body = """
<section class="hero">
  <h1>Everything a demo shop should be.<br>Flawed on purpose.</h1>
  <p>This little storefront is AutoQA's default test target: a realistic
     multi-page app with known defects, broken APIs and accessibility gaps —
     so every stage of the QA pipeline has something real to find.</p>
  <div class="cta">
    <a class="btn" href="/products">Browse products</a>
    <a class="btn ghost" style="color:#312e81;border-color:#c7d2fe" href="/dashboard">Open dashboard</a>
  </div>
  <form action='/search' class="searchbar">
    <input name='q' placeholder='Search products, orders, users…'>
    <button type='submit'>Search</button>
  </form>
</section>
<div class="grid cards">
  <a class="card" href="/dashboard"><span class="emoji">📊</span><h2>Dashboard</h2><p>Live stats fetched client-side from the demo APIs.</p></a>
  <a class="card" href="/products"><span class="emoji">🛒</span><h2>Products</h2><p>Catalog with a checkout that fails (HTTP 500 — on purpose).</p></a>
  <a class="card" href="/orders"><span class="emoji">📦</span><h2>Orders</h2><p>Order history with status chips.</p></a>
  <a class="card" href="/upload"><span class="emoji">📎</span><h2>Upload</h2><p>File upload flow for discovery and form testing.</p></a>
  <a class="card" href="/login"><span class="emoji">🔐</span><h2>Login</h2><p>Standard form login wired for AutoQA auth flows.</p></a>
  <a class="card" href="/broken"><span class="emoji">🧨</span><h2>Broken</h2><p>A page that throws a JS console error for evidence collection.</p></a>
</div>
"""
    return page("Home", body)


@app.get("/search", response_class=HTMLResponse)
def search(q: str = ""):
    # Flaw (Phase 10): q is reflected WITHOUT escaping — the security scanner's
    # benign reflection marker must appear raw in the response body.
    body = (
        "<section class='block'>"
        "<h1>Search</h1>"
        "<p class='sub'>Results are served from the catalog index.</p>"
        "<div class='card'><p>Results for: " + q + "</p>"
        "<p class='muted'>Try browsing the <a href='/products'>catalog</a> instead.</p></div>"
        "<span class='flaw'>⚠ Deliberate flaw: input reflected without encoding</span>"
        "</section>"
    )
    return page("Search", body)


_LOGIN_JS = """
<script>
document.getElementById('login-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  var box = document.getElementById('login-error');
  box.className = 'alert';
  try {
    var r = await fetch('/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
      body: new URLSearchParams(new FormData(e.target)).toString()
    });
    if (r.ok) {
      var d = await r.json();
      if (d && d.ok) { window.location.href = '/dashboard'; return; }
    }
    if (r.status >= 500) {
      box.textContent = 'Server error (HTTP ' + r.status + ') — a deliberate demo flaw.';
      console.error('Demo login failed: HTTP ' + r.status);
    } else {
      box.textContent = 'Login failed (HTTP ' + r.status + '): ' + (r.status === 400 ? 'missing credentials' : 'invalid credentials');
    }
    box.className = 'alert show';
  } catch (err) {
    box.textContent = 'Network error: ' + err;
    box.className = 'alert show';
    console.error('Demo login network error: ' + err);
  }
});
</script>
"""


@app.get("/login", response_class=HTMLResponse)
def login_form():
    body = """
<section class='block'>
  <h1>Sign in</h1>
  <p class='sub'>Welcome back — use any username and a non-empty password. The password <code>wrong</code> triggers a server error (on purpose).</p>
  <div class='card' style='max-width:460px'>
    <form id='login-form' method='post' action='/login' class='stack'>
      <input name='username' placeholder='Username' autocomplete='username'>
      <input name='password' type='password' placeholder='Password' autocomplete='current-password'>
      <button type='submit'>Login</button>
    </form>
    <div id='login-error' class='alert' role='alert'></div>
    <span class='flaw'>⚠ Deliberate flaws: unlabeled inputs · HTTP 500 on password "wrong"</span>
  </div>
</section>
""" + _LOGIN_JS
    return page("Login", body)


@app.post("/login")
def login(username: str = Form(""), password: str = Form("")):
    if password == "wrong":
        # Flaw: unhandled 500 for a "bad" password path
        raise RuntimeError("boom: bad password path")
    if username and password:
        return JSONResponse({"ok": True, "token": "demo-token"})
    return JSONResponse({"ok": False, "error": "missing credentials"}, status_code=400)


# ---------------------------------------------------------------------------
# Two-factor login fixture (OTP / 2FA) — target for AutoQA's OTP support
# ---------------------------------------------------------------------------
# Realistic two-step sign-in: password first, then a one-time code, then a
# real redirect to /dashboard (so `url_not_contains:login` passes only after
# the 2FA step). Accepted codes:
#   - a valid RFC 6238 TOTP for the demo authenticator secret below, or
#   - the static backdoor code 135790 (test-environment style).
TOTP_SECRET_B32 = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # ASCII "12345678901234567890"
OTP_BACKDOOR = "135790"


def _demo_totp(secret_b32: str, at: float | None = None) -> str:
    """RFC 6238 TOTP (SHA-1, 6 digits) — stdlib only, same algorithm AutoQA uses."""
    key = base64.b32decode(secret_b32 + "=" * ((8 - len(secret_b32) % 8) % 8))
    counter = int((time.time() if at is None else at) // 30)
    digest = hmac.new(key, struct.pack(">Q", counter), "sha1").digest()
    offset = digest[-1] & 0x0F
    code = (struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFFFFFF) % 10**6
    return str(code).zfill(6)


@app.get("/login-2fa", response_class=HTMLResponse)
def login_2fa_form():
    body = """
<section class='block'>
  <h1>Sign in <span style='font-size:.55em;color:#64748b'>(with 2FA)</span></h1>
  <p class='sub'>Step 1 of 2 — any username and a non-empty password. The next step asks for a one-time code.</p>
  <div class='card' style='max-width:460px'>
    <form method='post' action='/login-2fa' class='stack'>
      <input name='username' placeholder='Username' autocomplete='username'>
      <input name='password' type='password' placeholder='Password' autocomplete='current-password'>
      <button type='submit'>Continue</button>
    </form>
  </div>
</section>
"""
    return page("Login (2FA)", body)


@app.post("/login-2fa")
def login_2fa(username: str = Form(""), password: str = Form("")):
    if username and password:
        return RedirectResponse("/login-2fa/otp", status_code=303)
    return RedirectResponse("/login-2fa", status_code=303)


@app.get("/login-2fa/otp", response_class=HTMLResponse)
def login_2fa_otp(error: str = ""):
    alert = "<div class='alert' role='alert'>Invalid code — try again.</div>" if error else ""
    body = f"""
<section class='block'>
  <h1>Two-factor authentication</h1>
  <p class='sub'>Step 2 of 2 — enter the 6-digit code from your authenticator app.<br>
     Demo authenticator secret (base32): <code>{TOTP_SECRET_B32}</code> · static backdoor code: <code>{OTP_BACKDOOR}</code></p>
  <div class='card' style='max-width:460px'>
    <form method='post' action='/login-2fa/otp' class='stack'>
      <input name='otp' inputmode='numeric' autocomplete='one-time-code' placeholder='123456'>
      <button type='submit'>Verify</button>
    </form>
    {alert}
  </div>
</section>
"""
    return page("2FA code", body)


@app.post("/login-2fa/otp")
def login_2fa_verify(otp: str = Form("")):
    entered = otp.strip()
    now = time.time()
    valid = hmac.compare_digest(entered, OTP_BACKDOOR) or any(
        hmac.compare_digest(entered, _demo_totp(TOTP_SECRET_B32, at=t)) for t in (now - 30, now, now + 30)
    )
    if valid:
        return RedirectResponse("/dashboard", status_code=303)
    return RedirectResponse("/login-2fa/otp?error=1", status_code=303)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    # SPA-style behavior: page fetches APIs client-side (captured by the crawler
    # for API discovery). All fetched endpoints return 200 on purpose so the
    # console stays clean here — /broken is the page with the console flaw.
    body = """
<section class='block'>
  <h1>Dashboard</h1>
  <p class='sub'>Signed-in overview — numbers load client-side from the demo APIs.</p>
  <div class='grid cards'>
    <div class='card stat'><p>Users</p><span class='num' id='stat-users'>–</span></div>
    <div class='card stat'><p>Products</p><span class='num' id='stat-products'>–</span></div>
    <div class='card stat'><p>Orders</p><span class='num' id='stat-orders'>–</span></div>
    <div class='card stat'><p>Revenue</p><span class='num'>$1,024</span></div>
  </div>
  <div style='height:22px'></div>
  <table>
    <tr><th>User</th><th>Role</th><th>Status</th></tr>
    <tr><td>alice</td><td><span class='chip'>admin</span></td><td><span class='chip green'>active</span></td></tr>
    <tr><td>bob</td><td><span class='chip grey'>member</span></td><td><span class='chip amber'>invited</span></td></tr>
  </table>
  <span class='flaw'>⚠ Deliberate flaw: this page's inputs/labels exist for the a11y audit to flag on other pages</span>
</section>
<script>
  fetch('/api/users');
  fetch('/api/products');
  fetch('/api/orders/1');
  fetch('/api/orders');
  fetch('/api/stats');
</script>
"""
    return page("Dashboard", body)


@app.get("/users", response_class=HTMLResponse)
def users():
    body = """
<section class='block'>
  <h1>Users</h1>
  <p class='sub'>Registered accounts on this demo storefront.</p>
  <table>
    <tr><th>Name</th><th>Email</th><th>Status</th></tr>
    <tr><td>alice</td><td>alice@demo.test</td><td><span class='chip green'>active</span></td></tr>
    <tr><td>bob</td><td>bob@demo.test</td><td><span class='chip green'>active</span></td></tr>
  </table>
</section>
"""
    return page("Users", body)


_PRODUCTS_JS = """
<script>
async function addToCart(btn) {
  var box = document.getElementById('cart-error');
  box.className = 'alert';
  try {
    var r = await fetch('/api/checkout', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ items: [{ sku: btn.dataset.sku, price: Number(btn.dataset.price) }],
                             card_number: '4242424242424242', amount: btn.dataset.price })
    });
    if (!r.ok) {
      box.textContent = 'Checkout failed: HTTP ' + r.status + ' — order service down (deliberate demo flaw).';
      box.className = 'alert show';
      console.error('Demo checkout failed: HTTP ' + r.status);
    } else {
      box.textContent = 'Order placed!';
      box.className = 'alert ok show';
    }
  } catch (err) {
    box.textContent = 'Network error: ' + err;
    box.className = 'alert show';
    console.error('Demo checkout network error: ' + err);
  }
}
</script>
"""


@app.get("/products", response_class=HTMLResponse)
def products():
    items = [
        ("Widget", "🧩", "9.99"),
        ("Gadget", "🎧", "24.50"),
        ("Doohickey", "🔧", "14.00"),
        ("Sprocket", "⚙️", "6.75"),
        ("Gizmo", "🕹️", "39.00"),
        ("Thingamajig", "🪄", "19.90"),
    ]
    cards = "".join(
        "<div class='card'><span class='emoji'>" + emoji + "</span>"
        "<h3>" + name + "</h3><p>Classic demo item.</p>"
        "<p style='margin-top:10px'><span class='price'>$" + price + "</span></p>"
        "<button type='button' data-testid='add-to-cart' data-sku='" + name.lower()
        + "' data-price='" + price + "' onclick='addToCart(this)'>Add to cart</button>"
        "</div>"
        for name, emoji, price in items
    )
    body = (
        "<section class='block'>"
        "<h1>Products</h1>"
        "<p class='sub'>Six items ready to check out — checkout itself is the broken part.</p>"
        "<div class='grid cards'>" + cards + "</div>"
        "<div id='cart-error' class='alert' role='alert'></div>"
        "<div class='modal' id='cart-modal' hidden>"
        "<div class='card' style='max-width:380px'><h2>Cart</h2><p id='cart-contents'>Your cart is empty.</p></div>"
        "</div>"
        "<span class='flaw'>⚠ Deliberate flaw: POST /api/checkout always returns HTTP 500</span>"
        "</section>"
        + _PRODUCTS_JS
    )
    return page("Products", body)


@app.get("/orders", response_class=HTMLResponse)
def orders():
    body = """
<section class='block'>
  <h1>Orders</h1>
  <p class='sub'>Recent orders across the storefront.</p>
  <table>
    <tr><th>Order</th><th>Customer</th><th>Total</th><th>Status</th></tr>
    <tr><td>Order #1</td><td>alice</td><td>$24.50</td><td><span class='chip green'>shipped</span></td></tr>
    <tr><td>Order #2</td><td>bob</td><td>$9.99</td><td><span class='chip'>processing</span></td></tr>
    <tr><td>Order #3</td><td>alice</td><td>$39.00</td><td><span class='chip amber'>pending</span></td></tr>
  </table>
</section>
"""
    return page("Orders", body)


_UPLOAD_JS = """
<script>
document.getElementById('upload-form').addEventListener('submit', async function (e) {
  e.preventDefault();
  var box = document.getElementById('upload-result');
  box.className = 'alert';
  try {
    var r = await fetch('/upload', { method: 'POST', body: new FormData(e.target) });
    if (r.ok) {
      var d = await r.json();
      box.textContent = 'Uploaded ' + d.filename + ' (' + d.size + ' bytes)';
      box.className = 'alert ok show';
    } else {
      box.textContent = 'Upload rejected (HTTP ' + r.status + '): handled gracefully.';
      box.className = 'alert show';
    }
  } catch (err) {
    box.textContent = 'Network error: ' + err;
    box.className = 'alert show';
    console.error('Demo upload network error: ' + err);
  }
});
</script>
"""


@app.get("/upload", response_class=HTMLResponse)
def upload_form():
    body = """
<section class='block'>
  <h1>Upload</h1>
  <p class='sub'>Attach a file (avatar, import, receipt) — the endpoint echoes its metadata back.</p>
  <div class='card' style='max-width:520px'>
    <form id='upload-form' method='post' action='/upload' enctype='multipart/form-data' class='stack'>
      <input type='file' name='file'>
      <select name='category'>
        <option value='avatar'>Avatar</option>
        <option value='import'>Data import</option>
        <option value='receipt'>Receipt</option>
      </select>
      <button type='submit'>Upload file</button>
    </form>
    <div id='upload-result' class='alert' role='alert'></div>
    <span class='flaw'>⚠ Deliberate flaw: file input and select have no labels (a11y audit target)</span>
  </div>
</section>
""" + _UPLOAD_JS
    return page("Upload", body)


@app.post("/upload")
async def upload(file: UploadFile | None = File(None)):
    if file is None or not file.filename:
        return JSONResponse({"ok": False, "error": "no file provided"}, status_code=400)
    data = await file.read()
    return {"ok": True, "filename": file.filename, "size": len(data)}


@app.get("/broken", response_class=HTMLResponse)
def broken():
    # Flaw: page JS throws a console error (Phase 8 evidence collection)
    body = """
<section class='block'>
  <h1>Broken</h1>
  <p class='sub'>This page intentionally throws a JavaScript error when it loads.</p>
  <div class='card'>
    <span class='emoji'>🧨</span>
    <h2>Cart failed to load</h2>
    <p>The cart module crashed — check the browser console for the error AutoQA collects as evidence.</p>
  </div>
  <span class='flaw'>⚠ Deliberate flaw: console.error on page load</span>
</section>
<script>console.error('Demo JS failure: cart is undefined')</script>
<p id='cart' hidden>Cart</p>
"""
    return page("Broken", body)


# ---------------------------------------------------------------------------
# APIs (shapes are part of the platform's test contract)
# ---------------------------------------------------------------------------
@app.get("/api/users")
def api_users():
    return {"users": [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]}


@app.get("/api/products")
def api_products():
    return {"products": [{"id": 1, "name": "Widget"}]}


@app.get("/api/orders")
def api_orders():
    return {
        "orders": [
            {"id": 1, "status": "shipped"},
            {"id": 2, "status": "processing"},
            {"id": 3, "status": "pending"},
        ]
    }


@app.get("/api/stats")
def api_stats():
    return {"stats": {"users": 2, "products": 6, "orders": 3, "revenue": 1024}}


@app.post("/api/checkout")
async def checkout(request: Request):
    await request.json()
    # Flaw: checkout always fails with 500 (Phase 8 failure analysis target)
    return JSONResponse({"detail": "Internal Server Error: order service down"}, status_code=500)


@app.get("/api/orders/{order_id}")
def api_order(order_id: int):
    return {"id": order_id, "status": "shipped"}
