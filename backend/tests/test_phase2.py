"""Phase 2 unit tests: crawler logic (no network), controls, DOM extraction."""
from app.discovery.crawler import (
    CrawlControls,
    Crawler,
    _extract_components,
    _origin,
)


def test_origin_extraction():
    assert _origin("https://example.com/path?q=1") == "https://example.com"
    assert _origin("http://localhost:9000/x") == "http://localhost:9000"


def test_normalize_strips_fragment():
    c = Crawler(CrawlControls())
    assert c._normalize("https://x.com/a#section") == "https://x.com/a"
    assert c._normalize("https://x.com/a?b=1#frag") == "https://x.com/a?b=1"


def test_same_origin_enforced():
    c = Crawler(CrawlControls(same_origin=True))
    assert c._should_visit("https://x.com/a", "https://x.com", 0, set()) is True
    assert c._should_visit("https://evil.com/a", "https://x.com", 0, set()) is False


def test_max_depth_enforced():
    c = Crawler(CrawlControls(max_depth=2))
    assert c._should_visit("https://x.com/deep", "https://x.com", 3, set()) is False
    assert c._should_visit("https://x.com/ok", "https://x.com", 2, set()) is True


def test_max_urls_enforced():
    c = Crawler(CrawlControls(max_urls=1))
    c._seen.add("https://x.com/1")
    assert c._should_visit("https://x.com/2", "https://x.com", 0, set()) is False


def test_duplicate_detection():
    c = Crawler(CrawlControls())
    c._seen.add("https://x.com/a")
    assert c._should_visit("https://x.com/a", "https://x.com", 0, set()) is False


def test_robots_disallow_respected():
    c = Crawler(CrawlControls(respect_robots=True))
    robots = {"/admin"}
    assert c._should_visit("https://x.com/admin/panel", "https://x.com", 0, robots) is False
    assert c._should_visit("https://x.com/public", "https://x.com", 0, robots) is True


def test_extract_components_counts():
    html = """
    <form><input type='text'><input type='file'><select><option>1</option></select></form>
    <button>Go</button><button>Save</button>
    <table><tr><td>x</td></tr></table>
    <dialog class="modal">hi</dialog>
    <script src="a.js"></script>
    """
    comp = _extract_components(html)
    assert comp["forms"] == 1
    assert comp["inputs"] == 2
    assert comp["file_uploads"] == 1
    assert comp["buttons"] == 2
    assert comp["tables"] == 1
    assert comp["scripts"] == 1
