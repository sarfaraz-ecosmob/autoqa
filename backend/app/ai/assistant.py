"""AI QA assistant (spec §22) — Phase 14.

Answers questions from ACTUAL project data: every intent runs a real query
over the database and the answer is composed from the query result. The LLM
(when configured) may only rephrase/explain the grounded data — it never
invents numbers. With provider=none, deterministic heuristic answers are
returned, so the feature works offline.
"""
import sqlalchemy as sa

from app.ai import generate_json
from app.analysis.compare import compare_runs
from app.db import SessionLocal
from app.models import (
    AccessibilityResult,
    ApiEndpoint,
    Defect,
    PerformanceResult,
    Project,
    SecurityFinding,
    TestExecution,
    TestCase,
    TestRun,
)


def _pct(n: int, total: int) -> str:
    return f"{round(n * 100 / total, 1)}%" if total else "n/a"


def _q_failures(db, project_id: str, severity: str | None = None) -> dict:
    stmt = (
        sa.select(TestExecution, TestCase, Defect)
        .join(TestCase, TestCase.id == TestExecution.test_case_id)
        .join(Defect, Defect.execution_id == TestExecution.id, isouter=True)
        .where(TestExecution.test_run_id.in_(
            sa.select(TestRun.id).where(TestRun.project_id == project_id)
        ))
        .order_by(TestExecution.updated_at.desc())
        .limit(200)
    )
    rows = db.execute(stmt).all()
    failed = [(ex, case, defect) for ex, case, defect in rows if ex.status.value == "failed"]
    # Latest attempt per (ref, browser)
    latest: dict[tuple[str, str], tuple] = {}
    for ex, case, defect in failed:
        latest[(case.ref, ex.browser)] = (ex, case, defect)

    if severity:
        sev_rank = {"critical": 0, "high": 1, "medium": 2, "low": 3}
        want = sev_rank.get(severity.lower())
        if want is not None:
            latest = {
                k: v for k, v in latest.items()
                if v[2] is not None and sev_rank.get(v[2].severity.value, 9) <= want
            }
    items = [
        {
            "ref": case.ref,
            "browser": ex.browser,
            "module": case.module,
            "severity": defect.severity.value if defect else "untriaged",
            "root_cause": (defect.root_cause_ai or "")[:160] if defect else "",
            "run_id": ex.test_run_id,
        }
        for ex, case, defect in sorted(latest.values(), key=lambda v: v[1].ref)
    ]
    return {"count": len(items), "items": items[:20]}


def _q_module_defects(db, project_id: str) -> dict:
    rows = db.execute(
        sa.select(Defect.module, sa.func.count())
        .where(Defect.project_id == project_id)
        .group_by(Defect.module)
        .order_by(sa.func.count().desc())
    ).all()
    return {"modules": [{"module": m or "unknown", "defects": c} for m, c in rows]}


def _q_summary(db, project_id: str) -> dict:
    statuses = db.execute(
        sa.select(TestExecution.status)
        .where(TestExecution.test_run_id.in_(
            sa.select(TestRun.id).where(TestRun.project_id == project_id)
        ))
    ).scalars().all()
    counts: dict[str, int] = {}
    for s in statuses:
        counts[s.value] = counts.get(s.value, 0) + 1
    total = sum(counts.values())
    runs = db.execute(
        sa.select(sa.func.count()).select_from(TestRun).where(TestRun.project_id == project_id)
    ).scalar_one()
    defects = db.execute(
        sa.select(sa.func.count()).select_from(Defect).where(Defect.project_id == project_id)
    ).scalar_one()
    return {
        "total_executions": total,
        "passed": counts.get("passed", 0),
        "failed": counts.get("failed", 0),
        "skipped": counts.get("skipped", 0),
        "pass_rate": _pct(counts.get("passed", 0), total),
        "runs": runs,
        "defects": defects,
    }


def _q_api_latency(db, project_id: str) -> dict:
    rows = db.execute(
        sa.select(ApiEndpoint)
        .where(ApiEndpoint.project_id == project_id, ApiEndpoint.avg_response_ms.isnot(None))
        .order_by(ApiEndpoint.avg_response_ms.desc())
        .limit(5)
    ).scalars().all()
    return {
        "apis": [
            {"method": a.method, "path": a.path, "avg_ms": a.avg_response_ms}
            for a in rows
        ]
    }


def _q_compare(db, project_id: str, label_a: str, label_b: str) -> dict:
    """Find the two most recent runs (or by label substring) and diff them."""
    runs = db.execute(
        sa.select(TestRun).where(TestRun.project_id == project_id).order_by(TestRun.created_at.desc()).limit(10)
    ).scalars().all()
    if len(runs) < 2:
        return {"error": "need at least 2 runs to compare"}
    if label_a or label_b:
        match_a = next((r for r in runs if label_a.lower() in r.label.lower()), None)
        match_b = next((r for r in runs if label_b.lower() in r.label.lower()), None)
        if not match_a or not match_b or match_a.id == match_b.id:
            return {"error": "could not match both labels; ask without labels to compare the two latest runs"}
        return compare_runs(match_a.id, match_b.id)
    return compare_runs(runs[1].id, runs[0].id)  # older = base, newer = target


def _q_security(db, project_id: str) -> dict:
    rows = db.execute(
        sa.select(SecurityFinding.severity, sa.func.count())
        .where(SecurityFinding.project_id == project_id)
        .group_by(SecurityFinding.severity)
    ).all()
    return {"by_severity": {s.value: c for s, c in rows}}


def _q_a11y(db, project_id: str) -> dict:
    rows = db.execute(
        sa.select(AccessibilityResult).where(AccessibilityResult.project_id == project_id)
    ).scalars().all()
    total = sum(len(r.violations or []) for r in rows)
    rules: dict[str, int] = {}
    for r in rows:
        for v in r.violations or []:
            rule = v.get("rule", "?") if isinstance(v, dict) else "?"
            rules[rule] = rules.get(rule, 0) + 1
    return {
        "pages": len(rows),
        "violations": total,
        "top_rules": sorted(rules.items(), key=lambda kv: -kv[1])[:6],
    }


def _q_regression(db, project_id: str, module: str | None) -> dict:
    """List approved cases (optionally per module) as a regression seed."""
    stmt = sa.select(TestCase).where(
        TestCase.project_id == project_id, TestCase.approved.is_(True)
    )
    if module:
        stmt = stmt.where(TestCase.module.ilike(f"%{module}%"))
    rows = db.execute(stmt.order_by(TestCase.priority, TestCase.ref)).scalars().all()
    return {
        "module": module or "all",
        "cases": [
            {"ref": c.ref, "scenario": c.scenario[:80], "priority": c.priority.value}
            for c in rows[:25]
        ],
    }


# ---------- intent routing ----------

_INTENTS = [
    ("compare", ("compare", "vs", "versus", "difference between")),
    ("why_failed", ("why", "failed", "failure", "root cause", "broken")),
    ("critical", ("critical", "severe", "high severity", "important failures")),
    ("summary", ("summary", "overview", "status", "how did", "pass rate", "client")),
    ("module_defects", ("most defects", "worst module", "module has", "defects by")),
    ("api_latency", ("response time", "latency", "slowest", "api time")),
    ("security", ("security", "vulnerab", "finding")),
    ("a11y", ("accessibility", "a11y", "wcag", "contrast")),
    ("regression", ("regression", "generate test", "suite for")),
]


def _route(question: str) -> tuple[str, dict]:
    q = question.lower()
    for intent, keywords in _INTENTS:
        if any(k in q for k in keywords):
            extras = {}
            if intent == "compare":
                # "compare run A and run B"
                parts = q.split()
                label_a = label_b = ""
                for i, w in enumerate(parts):
                    if w in ("and", "vs", "versus", "with") and 0 < i < len(parts) - 1:
                        label_a, label_b = parts[i - 1], parts[i + 1]
                extras = {"label_a": label_a, "label_b": label_b}
            if intent == "regression":
                module = None
                for m in ("checkout", "login", "auth", "form", "nav", "search", "order"):
                    if m in q:
                        module = m
                        break
                extras = {"module": module}
            if intent == "why_failed":
                extras = {"ref": next((w.upper() for w in q.split() if w.upper().startswith("TC-")), "")}
            return intent, extras
    return "summary", {}


def answer_question(project_id: str, question: str) -> dict:
    """Grounded answer: real queries → heuristic composition → optional LLM phrasing."""
    db = SessionLocal()
    try:
        intent, extras = _route(question)

        if intent == "compare":
            data = _q_compare(db, project_id, extras.get("label_a", ""), extras.get("label_b", ""))
        elif intent == "critical":
            data = _q_failures(db, project_id, severity="high")
        elif intent == "why_failed":
            data = _q_failures(db, project_id)
            ref = extras.get("ref", "")
            if ref:
                data = {"count": 1, "items": [i for i in data["items"] if i["ref"].startswith(ref)][:5] or data["items"][:1]}
            else:
                data = {"count": data["count"], "items": data["items"][:5]}
        elif intent == "module_defects":
            data = _q_module_defects(db, project_id)
        elif intent == "api_latency":
            data = _q_api_latency(db, project_id)
        elif intent == "security":
            data = _q_security(db, project_id)
        elif intent == "a11y":
            data = _q_a11y(db, project_id)
        elif intent == "regression":
            data = _q_regression(db, project_id, extras.get("module"))
        else:
            intent = "summary"
            data = _q_summary(db, project_id)

        project = db.execute(sa.select(Project).where(Project.id == project_id)).scalar_one_or_none()
        answer = _compose(intent, data, project.name if project else "project")

        # Optional LLM rephrasing — grounded data only, never new facts
        llm = generate_json(
            system=(
                "You are a QA assistant. You are given verified data from the QA database "
                "and a draft answer. Rephrase the draft concisely for the user's question. "
                "You MUST NOT add, change, or invent any numbers, test refs, or findings. "
                'Return JSON: {"answer": "..."}'
            ),
            user=f"question: {question}\nverified data: {data}\ndraft: {answer}",
            fallback={},
        )
        if isinstance(llm, dict) and llm.get("answer"):
            answer = llm["answer"]

        return {"intent": intent, "answer": answer, "data": data, "grounded": True}
    finally:
        db.close()


def _compose(intent: str, data: dict, project_name: str) -> str:
    if intent == "summary":
        if not data["total_executions"]:
            return f"No test executions recorded for {project_name} yet — start a run first."
        return (
            f"{project_name}: {data['total_executions']} executions across {data['runs']} run(s) — "
            f"{data['passed']} passed, {data['failed']} failed ({data['pass_rate']} pass rate), "
            f"{data['defects']} defect(s) triaged."
        )
    if intent in ("why_failed", "critical"):
        if not data["items"]:
            return "No failures match — everything is green (or untriaged)."
        lines = [f"{i['ref']} ({i['browser']}, {i['severity']})" + (f": {i['root_cause']}" if i["root_cause"] else "") for i in data["items"][:6]]
        head = f"{data['count']} failure(s)" if intent == "critical" else "Latest failures"
        return f"{head} — " + "; ".join(lines)
    if intent == "module_defects":
        if not data["modules"]:
            return "No defects recorded yet."
        top = data["modules"][0]
        rest = ", ".join(f"{m['module']}: {m['defects']}" for m in data["modules"][1:3])
        return f"{top['module']} has the most defects ({top['defects']})." + (f" Followed by {rest}." if rest else "")
    if intent == "api_latency":
        if not data["apis"]:
            return "No API latency data yet — run a scan or API tests first."
        top = data["apis"][0]
        return (
            f"Slowest API: {top['method']} {top['path']} at {top['avg_ms']}ms avg. "
            + ", ".join(f"{a['method']} {a['path']}: {a['avg_ms']}ms" for a in data["apis"][1:3])
        )
    if intent == "security":
        if not data["by_severity"]:
            return "No security findings recorded — run a security scan first."
        return "Security findings by severity: " + ", ".join(f"{k}: {v}" for k, v in sorted(data["by_severity"].items()))
    if intent == "a11y":
        if not data["pages"]:
            return "No accessibility audit yet — run one from the Quality tab."
        return (
            f"Accessibility: {data['violations']} violations across {data['pages']} page(s). "
            f"Top rules: {', '.join(f'{r} ({c})' for r, c in data['top_rules'])}."
        )
    if intent == "regression":
        if not data["cases"]:
            return "No approved test cases match — generate and approve cases first."
        return f"Regression seed for '{data['module']}' ({len(data['cases'])} approved case(s)): " + ", ".join(c["ref"] for c in data["cases"][:10])
    if intent == "compare":
        if "error" in data:
            return data["error"]
        b, t = data["base"], data["target"]
        return (
            f"{b['label']} → {t['label']}: failures {b['failed']} → {t['failed']} "
            f"({len(data['new_failures'])} new, {len(data['resolved_failures'])} resolved, "
            f"{len(data['persistent_failures'])} persistent); "
            f"test set changed by {len(data['new_tests']) + len(data['removed_tests'])}."
        )
    return "Ask about failures, defects by module, API latency, security, accessibility, run comparison, or a summary."
