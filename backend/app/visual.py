"""Visual testing engine (Percy-style, Tier-1).

Baselines are approved screenshots per (test case, browser). Each execution
with a screenshot can be compared: pixel diff via Pillow, percentage of
changed pixels, and a red-highlighted diff image. All three images (baseline,
current, diff) live in the shared artifacts storage.

Storage keys:
    visual/{project}/{case}/{browser}/baseline.png
    visual/{project}/{case}/{browser}/current-{execution}.png
    visual/{project}/{case}/{browser}/diff-{execution}.png
"""
import base64
import io

from app import storage
from app.models import Project, TestCase, VisualBaseline, VisualCheck
from app.storage import checksum as sha256

DIFF_THRESHOLD = 0.5  # % changed pixels above which a check fails


def store_baseline(db, project: Project, case: TestCase, browser: str, image_b64: str) -> dict:
    """Decode + persist a new baseline, replacing any existing one."""
    try:
        png = base64.b64decode(body_b64(image_b64), validate=True)
    except Exception:
        raise ValueError("image_b64 is not valid base64 PNG data")
    dims = _dimensions(png)
    if dims is None:
        raise ValueError("image is not a valid PNG")

    key = _baseline_key(project.id, case.id, browser)
    storage.put_bytes(key, png, "image/png")
    row = db.execute(
        _baseline_select(case.id, browser)
    ).scalar_one_or_none()
    if row is None:
        row = VisualBaseline(project_id=project.id, test_case_id=case.id, browser=browser)
        db.add(row)
    row.storage_key = key
    row.width, row.height = dims
    row.checksum = sha256(png)
    db.commit()
    return {
        "id": str(row.id),
        "test_case_id": str(case.id),
        "browser": browser,
        "storage_key": key,
        "width": row.width,
        "height": row.height,
    }


def compare_execution(db, project_id: str, case_id: str, execution_id: str, browser: str, shot_key: str) -> dict:
    """Compare an execution screenshot against the baseline. Creates a
    VisualCheck row. Never raises — visual checks must not break execution."""
    try:
        db_session = db
        baseline = db_session.execute(
            _baseline_select(case_id, browser)
        ).scalar_one_or_none()

        current_key = f"visual/{project_id}/{case_id}/{browser}/current-{execution_id}.png"
        try:
            current_png = storage.get_bytes(shot_key)
        except Exception:
            return {"status": "skipped", "reason": "screenshot unreadable"}

        # copy current into the visual namespace so checks are self-contained
        storage.put_bytes(current_key, current_png, "image/png")

        if baseline is None:
            check = VisualCheck(
                project_id=project_id,
                test_case_id=case_id,
                execution_id=execution_id,
                browser=browser,
                current_key=current_key,
                status="new",  # no baseline yet — this shot can become one
            )
            db_session.add(check)
            db_session.commit()
            return {"status": "new", "check_id": str(check.id)}

        baseline_png = storage.get_bytes(baseline.storage_key)
        diff_png, diff_percent = _pixel_diff(baseline_png, current_png)

        status = "passed" if diff_percent <= DIFF_THRESHOLD else "failed"
        diff_key = ""
        if status == "failed":
            diff_key = f"visual/{project_id}/{case_id}/{browser}/diff-{execution_id}.png"
            storage.put_bytes(diff_key, diff_png, "image/png")

        check = VisualCheck(
            project_id=project_id,
            test_case_id=case_id,
            execution_id=execution_id,
            browser=browser,
            baseline_key=baseline.storage_key,
            current_key=current_key,
            diff_key=diff_key,
            status=status,
            diff_percent=round(diff_percent, 4),
        )
        db_session.add(check)
        db_session.commit()
        return {"status": status, "diff_percent": round(diff_percent, 4), "check_id": str(check.id)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "skipped", "reason": str(exc)[:120]}


def approve_check_baseline(db, project: Project, check: VisualCheck) -> dict:
    """Approve a check's current screenshot as the new baseline."""
    case = db.get(TestCase, check.test_case_id)
    if case is None:
        raise ValueError("test case missing")
    return store_baseline(
        db, project, case, check.browser, _b64_of(check.current_key)
    )


# ---------------------------------------------------------------- internals


def _baseline_select(case_id: str, browser: str):
    import sqlalchemy as sa

    return sa.select(VisualBaseline).where(
        VisualBaseline.test_case_id == case_id,
        VisualBaseline.browser == browser,
    )


def _baseline_key(project_id: str, case_id: str, browser: str) -> str:
    return f"visual/{project_id}/{case_id}/{browser}/baseline.png"


def _b64_of(storage_key: str) -> str:
    return base64.b64encode(storage.get_bytes(storage_key)).decode()


def body_b64(image_b64: str) -> bytes:
    """Accept both raw base64 and data URLs."""
    if image_b64.startswith("data:"):
        image_b64 = image_b64.split(",", 1)[1]
    return image_b64.encode()


def _dimensions(png: bytes) -> tuple[int, int] | None:
    try:
        from PIL import Image

        with Image.open(io.BytesIO(png)) as im:
            return im.size
    except Exception:
        return None


def _pixel_diff(a_png: bytes, b_png: bytes) -> tuple[bytes, float]:
    """Pixel diff via Pillow. Returns (diff_png, percent_changed).

    Highlights changed pixels in red over a faded blend of both images.
    Different sizes count all extra rows/columns as changed (Percy behaves
    the same way for layout shifts).
    """
    from PIL import Image, ImageChops, ImageDraw

    a = Image.open(io.BytesIO(a_png)).convert("RGB")
    b = Image.open(io.BytesIO(b_png)).convert("RGB")
    width, height = max(a.width, b.width), max(a.height, b.height)
    a = _fit(a, width, height)
    b = _fit(b, width, height)

    diff = ImageChops.difference(a, b).convert("L")
    bbox = diff.getbbox()
    total = width * height
    if bbox is None:
        return b"", 0.0

    hist = diff.histogram()
    changed = sum(hist[8:])  # tolerate tiny anti-aliasing noise (<8/255)
    percent = (changed * 100.0) / total

    # build the review image: faded current + red boxes on changed regions
    out = Image.blend(a, b, 0.5)
    overlay = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    draw = ImageDraw.Draw(overlay)
    mask = diff.point(lambda p: 255 if p >= 8 else 0)
    from PIL import Image as _Image

    red = _Image.new("RGBA", (width, height), (220, 38, 38, 140))
    overlay = _Image.composite(red, overlay, mask.convert("L"))
    out = Image.alpha_composite(out.convert("RGBA"), overlay).convert("RGB")

    buf = io.BytesIO()
    out.save(buf, format="PNG")
    return buf.getvalue(), percent


def _fit(im, width: int, height: int):
    from PIL import Image

    if im.width == width and im.height == height:
        return im
    canvas = Image.new("RGB", (width, height), (255, 255, 255))
    canvas.paste(im, (0, 0))
    return canvas
