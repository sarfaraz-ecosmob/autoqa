"""Requirements ingestion: parse uploaded documents into requirement drafts.

Supported: Markdown/plain text (heading or blank-line sections), Jira JSON
export, CSV/XLSX (header-driven), PDF (pypdf text extraction), DOCX
(python-docx paragraphs). Everything degrades gracefully: unknown types raise
ValueError; parsers never invent requirements from empty content.
"""
from __future__ import annotations

import csv
import io
import json
import re
from dataclasses import dataclass, field

# REQ-[A-Z0-9-]+ captured anywhere in a section (e.g. REQ-AUTH-001, REQ-12)
_REQ_TOKEN = re.compile(r"\bREQ-[A-Z0-9]+(?:-[A-Z0-9]+)*\b", re.IGNORECASE)
# Optional explicit title line: "REQ-AUTH-001: User can reset password"
_ID_TITLE = re.compile(
    r"^\s*(?P<id>REQ-[A-Z0-9]+(?:-[A-Z0-9]+)*)\s*[:\-.]\s*(?P<rest>.+)$", re.IGNORECASE
)

MAX_TITLE = 300
MAX_DESCRIPTION = 8000


@dataclass
class RequirementDraft:
    external_id: str
    title: str
    description: str
    source: str = "document"
    source_ref: str | None = None
    tags: list[str] = field(default_factory=list)


def parse_document(filename: str, data: bytes) -> list[RequirementDraft]:
    """Dispatch on extension; returns normalized drafts (no DB access)."""
    name = (filename or "").lower()
    if name.endswith((".md", ".markdown", ".txt")):
        return _from_text(data.decode("utf-8", errors="replace"))
    if name.endswith(".json"):
        return _from_json(data.decode("utf-8", errors="replace"))
    if name.endswith(".csv"):
        return _from_csv(io.StringIO(data.decode("utf-8-sig", errors="replace")))
    if name.endswith((".xlsx", ".xls")):
        return _from_xlsx(data)
    if name.endswith(".pdf"):
        return _from_pdf(data)
    if name.endswith(".docx"):
        return _from_docx(data)
    raise ValueError(
        "unsupported document type — use .md .txt .pdf .docx .xlsx .csv or .json"
    )


# ---------------------------------------------------------------- text / md

def _split_sections(text: str) -> list[tuple[str | None, str]]:
    """Split into (heading|None, body) chunks: markdown headings, else blank lines."""
    if re.search(r"^#{1,6}\s+\S", text, re.MULTILINE):
        parts = re.split(r"^#{1,6}\s+", text, flags=re.MULTILINE)
        sections: list[tuple[str | None, str]] = []
        for chunk in parts:
            if not chunk.strip():
                continue
            lines = chunk.splitlines()
            sections.append((lines[0].strip(), "\n".join(lines[1:]).strip()))
        return sections
    blocks = re.split(r"\n\s*\n", text)
    return [(None, b.strip()) for b in blocks if b.strip()]


def _from_text(text: str) -> list[RequirementDraft]:
    drafts: list[RequirementDraft] = []
    for heading, body in _split_sections(text):
        blob = "\n".join(x for x in (heading, body) if x)
        ids = _REQ_TOKEN.findall(blob)
        external_id = ids[0].upper() if ids else ""
        title = heading or ""
        desc = body
        if not heading:
            lines = [ln for ln in body.splitlines() if ln.strip()]
            if not lines:
                continue
            m = _ID_TITLE.match(lines[0])
            if m:
                external_id = external_id or m.group("id").upper()
                title = m.group("rest").strip()
                desc = "\n".join(lines[1:]).strip()
            else:
                title = lines[0].strip()
                desc = "\n".join(lines[1:]).strip()
        else:
            m = _ID_TITLE.match(title)
            if m:
                external_id = external_id or m.group("id").upper()
                title = m.group("rest").strip()
        if not title:
            continue
        drafts.append(
            RequirementDraft(
                external_id=external_id,
                title=title[:MAX_TITLE],
                description=desc[:MAX_DESCRIPTION],
                source="document",
            )
        )
    return drafts


# ---------------------------------------------------------------- jira json

def _from_json(raw: str) -> list[RequirementDraft]:
    data = json.loads(raw)
    issues = data.get("issues") if isinstance(data, dict) else data
    if not isinstance(issues, list):
        raise ValueError("JSON must be a Jira export ({issues: [...]}) or a list of requirements")

    out: list[RequirementDraft] = []
    for it in issues:
        if not isinstance(it, dict):
            continue
        if "fields" in it:  # Jira export
            f = it.get("fields") or {}
            key = it.get("key") or ""
            title = (f.get("summary") or "").strip()
            desc = _strip_jira_wiki(str(f.get("description") or ""))
        else:  # flat requirement shape
            key = str(it.get("external_id") or it.get("id") or "")
            title = str(it.get("title") or it.get("summary") or "").strip()
            desc = str(it.get("description") or "")
        if not title:
            continue
        m = _REQ_TOKEN.search(key)
        out.append(
            RequirementDraft(
                external_id=(m.group(0).upper() if m else key.upper()),
                title=title[:MAX_TITLE],
                description=desc[:MAX_DESCRIPTION],
                source="jira" if "fields" in it else "document",
                source_ref=key or None,
            )
        )
    return out


def _strip_jira_wiki(text: str) -> str:
    text = re.sub(r"\{code[^}]*\}.*?\{code\}", "", text, flags=re.DOTALL)
    text = re.sub(r"\{noformat[^}]*\}.*?\{noformat\}", "", text, flags=re.DOTALL)
    text = re.sub(r"!\S+?(?:\|thumbnail)!", "", text)  # images
    text = re.sub(r"\[([^\]|]+)\|[^\]]+\]", r"\1", text)  # [label|url] → label
    text = re.sub(r"\[([^\]]+)\]", r"\1", text)
    text = re.sub(r"\{\*?([^}]+)\}", r"\1", text)  # {color:..} {mono}
    text = re.sub(r"h[1-6]\.\s*", "", text)
    text = re.sub(r"^\s*[-*#]+\s*", "", text, flags=re.MULTILINE)
    return text.strip()


# ---------------------------------------------------------------- csv / xlsx

_ID_COLS = {"id", "req", "req_id", "key", "issue_key", "ref", "external_id", "requirement_id"}
_TITLE_COLS = {"title", "summary", "name", "requirement", "headline"}
_DESC_COLS = {"description", "detail", "details", "body", "text", "acceptance", "acceptance_criteria"}


def _pick(cols: dict[str, str], wanted: set[str]) -> str | None:
    for c, norm in cols.items():
        if norm in wanted:
            return c
    return None


def _rows_to_drafts(rows: list[dict]) -> list[RequirementDraft]:
    if not rows:
        return []
    norm = {c: re.sub(r"[^a-z0-9]", "", c.lower()) for c in rows[0]}
    id_c, title_c, desc_c = _pick(norm, _ID_COLS), _pick(norm, _TITLE_COLS), _pick(norm, _DESC_COLS)
    if not title_c:
        # fall back: first non-id column is the title, rest is description
        candidates = [c for c in rows[0] if c != id_c]
        if not candidates:
            return []
        title_c = candidates[0]
        desc_c = next((c for c in candidates[1:]), None)
    drafts: list[RequirementDraft] = []
    for row in rows:
        title = str(row.get(title_c) or "").strip()
        if not title:
            continue
        raw_id = str(row.get(id_c) or "").strip() if id_c else ""
        m = _REQ_TOKEN.search(raw_id) or _REQ_TOKEN.search(title)
        drafts.append(
            RequirementDraft(
                external_id=(m.group(0).upper() if m else raw_id.upper()),
                title=title[:MAX_TITLE],
                description=str(row.get(desc_c) or "").strip()[:MAX_DESCRIPTION] if desc_c else "",
                source="document",
                source_ref=raw_id or None,
            )
        )
    return drafts


def _from_csv(stream: io.StringIO) -> list[RequirementDraft]:
    reader = csv.DictReader(stream)
    return _rows_to_drafts([dict(r) for r in reader])


def _from_xlsx(data: bytes) -> list[RequirementDraft]:
    import openpyxl  # already a dependency (xlsx report generator)

    wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
    ws = wb.active
    rows = list(ws.iter_rows(values_only=True))
    if not rows:
        return []
    header = [str(c).strip() if c is not None else f"col{i}" for i, c in enumerate(rows[0])]
    dict_rows = []
    for r in rows[1:]:
        if all(c is None or str(c).strip() == "" for c in r):
            continue
        dict_rows.append({header[i]: ("" if i >= len(r) or r[i] is None else r[i]) for i in range(len(header))})
    return _rows_to_drafts(dict_rows)


# ---------------------------------------------------------------- pdf / docx

def _from_pdf(data: bytes) -> list[RequirementDraft]:
    try:
        from pypdf import PdfReader
    except ImportError as exc:  # pragma: no cover
        raise ValueError("PDF support requires the 'pypdf' package") from exc
    reader = PdfReader(io.BytesIO(data))
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    return _from_text(text)


def _from_docx(data: bytes) -> list[RequirementDraft]:
    try:
        import docx  # python-docx
    except ImportError as exc:  # pragma: no cover
        raise ValueError("DOCX support requires the 'python-docx' package") from exc
    document = docx.Document(io.BytesIO(data))
    lines: list[str] = []
    for para in document.paragraphs:
        style = (para.style.name or "").lower() if para.style is not None else ""
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        if style.startswith("heading"):
            lines.append(f"# {text}")
        else:
            lines.append(text)
    return _from_text("\n".join(lines))


# ---------------------------------------------------------------- normalize

def dedupe_and_normalize(
    drafts: list[RequirementDraft], existing_ids: set[str] | None = None
) -> tuple[list[RequirementDraft], list[str]]:
    """Trim empties, dedupe by external_id (or generated one), return (drafts, skipped_ids).

    external_ids are unique per project — ids already present in the DB and
    duplicates inside the document are reported as skipped.
    """
    existing = set(existing_ids or set())
    seen: set[str] = existing
    kept: list[RequirementDraft] = []
    skipped: list[str] = []
    gen = 0
    for d in drafts:
        title = (d.title or "").strip()
        if not title and not (d.description or "").strip():
            continue
        if not title:
            title = (d.description or "").strip().splitlines()[0][:MAX_TITLE]
        ext = (d.external_id or "").strip().upper()
        if not ext:
            gen += 1
            ext = f"REQ-GEN-{gen:03d}"
        if ext in seen:
            skipped.append(ext)
            continue
        seen.add(ext)
        kept.append(
            RequirementDraft(
                external_id=ext[:60],
                title=title[:MAX_TITLE],
                description=(d.description or "").strip()[:MAX_DESCRIPTION],
                source=d.source or "document",
                source_ref=d.source_ref,
                tags=list(d.tags or []),
            )
        )
    return kept, skipped
