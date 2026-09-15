"""Demo application — AutoQA's default test target (spec §30).

Deliberately includes flaws for every phase to detect:
  - Phase 2 crawler: multi-page site, forms, links
  - Phase 5/6: login flow, broken checkout endpoint (HTTP 500)
  - Phase 8: JS console error on /broken
  - Phase 10: missing security headers, reflected input
"""
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse

app = FastAPI(title="Demo Shop", docs_url="/api-docs")

PAGE = """<!doctype html><html><head><title>Demo Shop - {title}</title></head>
<body>
<nav><a href="/">Home</a> | <a href="/login">Login</a> | <a href="/dashboard">Dashboard</a>
| <a href="/users">Users</a> | <a href="/products">Products</a> | <a href="/orders">Orders</a>
| <a href="/broken">Broken</a></nav>
<h1>{title}</h1>
{body}
</body></html>"""


def page(title: str, body: str) -> HTMLResponse:
    return HTMLResponse(PAGE.format(title=title, body=body))


@app.get("/health")
def health() -> dict:
    return {"status": "ok"}


@app.get("/", response_class=HTMLResponse)
def home():
    return page("Home", "<p>Welcome to Demo Shop.</p><form action='/search'><input name='q'><button>Search</button></form>")


@app.get("/search", response_class=HTMLResponse)
def search(q: str = ""):
    # Flaw: reflected input without escaping (XSS indicator for Phase 10)
    return page("Search", f"<p>Results for: {q}</p>")


@app.get("/login", response_class=HTMLResponse)
def login_form():
    return page("Login", """
    <form method='post' action='/login'>
      <input name='username' placeholder='username'>
      <input name='password' type='password' placeholder='password'>
      <button type='submit'>Login</button>
    </form>""")


@app.post("/login")
def login(username: str = Form(""), password: str = Form("")):
    if password == "wrong":
        # Flaw: unhandled 500 for a "bad" password path
        raise RuntimeError("boom: bad password path")
    if username and password:
        return JSONResponse({"ok": True, "token": "demo-token"})
    return JSONResponse({"ok": False, "error": "missing credentials"}, status_code=400)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return page("Dashboard", "<table><tr><th>User</th></tr><tr><td>alice</td></tr></table>")


@app.get("/users", response_class=HTMLResponse)
def users():
    return page("Users", "<ul><li>alice</li><li>bob</li></ul>")


@app.get("/products", response_class=HTMLResponse)
def products():
    return page("Products", "<ul><li>Widget</li><li>Gadget</li></ul>")


@app.get("/orders", response_class=HTMLResponse)
def orders():
    return page("Orders", "<ul><li>Order #1</li></ul>")


@app.get("/broken", response_class=HTMLResponse)
def broken():
    # Flaw: page JS throws a console error (Phase 8 evidence collection)
    return page("Broken", "<script>console.error('Demo JS failure: cart is undefined')</script><p id='cart'>Cart</p>")


@app.get("/api/users")
def api_users():
    return {"users": [{"id": 1, "name": "alice"}, {"id": 2, "name": "bob"}]}


@app.get("/api/products")
def api_products():
    return {"products": [{"id": 1, "name": "Widget"}]}


@app.post("/api/checkout")
async def checkout(request: Request):
    await request.json()
    # Flaw: checkout always fails with 500 (Phase 8 failure analysis target)
    return JSONResponse({"detail": "Internal Server Error: order service down"}, status_code=500)


@app.get("/api/orders/{order_id}")
def api_order(order_id: int):
    return {"id": order_id, "status": "shipped"}
