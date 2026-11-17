"""The knowledge base: Markdown documents of a product, split into sections (phase 11).

`knowledge/<product>/*.md` is imported with `uv run python -m app.knowledge_import`. Search uses
Postgres full-text search in Italian; on other databases (the tests) a plain word match.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, select, text

from app.db import session as db
from app.db.models import KnowledgeChunk
from app.memory import _stems

MAX_CHUNK_CHARS = 1500
HEADING = re.compile(r"^(#{1,4})\s+(.*\S)\s*$")


@dataclass
class Hit:
    product: str
    source: str
    heading: str
    text: str


def split_markdown(markdown: str) -> list[tuple[str, str]]:
    """(heading path, text) sections. A long section is cut at paragraph breaks."""
    path: list[tuple[int, str]] = []
    sections: list[tuple[str, list[str]]] = [("", [])]
    for line in markdown.splitlines():
        m = HEADING.match(line)
        if m:
            level = len(m.group(1))
            path = [(lv, t) for lv, t in path if lv < level] + [(level, m.group(2))]
            sections.append((" > ".join(t for _, t in path), []))
        else:
            sections[-1][1].append(line)
    out: list[tuple[str, str]] = []
    for heading, lines in sections:
        body = "\n".join(lines).strip()
        if not body:
            continue
        chunk = ""
        for para in re.split(r"\n\s*\n", body):
            if chunk and len(chunk) + len(para) > MAX_CHUNK_CHARS:
                out.append((heading, chunk.strip()))
                chunk = ""
            while len(para) > MAX_CHUNK_CHARS:  # one huge paragraph
                out.append((heading, para[:MAX_CHUNK_CHARS]))
                para = para[MAX_CHUNK_CHARS:]
            chunk += para + "\n\n"
        if chunk.strip():
            out.append((heading, chunk.strip()))
    return out


async def import_file(product: str, source: str, markdown: str) -> int:
    """Replace everything stored for (product, source) with the sections of `markdown`."""
    sections = split_markdown(markdown)
    async with db.session_maker()() as s:
        await s.execute(
            delete(KnowledgeChunk).where(
                KnowledgeChunk.product == product, KnowledgeChunk.source == source
            )
        )
        now = datetime.now(UTC)
        for heading, body in sections:
            s.add(
                KnowledgeChunk(
                    product=product, source=source, heading=heading[:300], text=body, updated_at=now
                )
            )
        await s.commit()
    return len(sections)


async def import_dir(root: Path) -> dict[str, int]:
    """Import every knowledge/<product>/**/*.md; returns sections per product."""
    counts: dict[str, int] = {}
    for product_dir in sorted(p for p in root.iterdir() if p.is_dir()):
        for md in sorted(product_dir.rglob("*.md")):
            rel = md.relative_to(product_dir).as_posix()
            n = await import_file(product_dir.name, rel, md.read_text(encoding="utf-8"))
            counts[product_dir.name] = counts.get(product_dir.name, 0) + n
    return counts


async def search(product: str, query: str, limit: int = 5) -> list[Hit]:
    """The best passages for `query` in the documents of `product`, with their source file."""
    words = [w for w in re.findall(r"[^\W_]{3,}", query.lower())][:12]
    if not words:
        return []
    async with db.session_maker()() as s:
        rows: list[KnowledgeChunk]
        if s.bind.dialect.name == "postgresql":
            q = " | ".join(words)
            res = await s.execute(
                text(
                    "SELECT id FROM knowledge_chunks WHERE lower(product) = lower(:p) AND "
                    "to_tsvector('italian', heading || ' ' || text) @@ to_tsquery('italian', :q) "
                    "ORDER BY ts_rank(to_tsvector('italian', heading || ' ' || text), "
                    "to_tsquery('italian', :q)) DESC LIMIT :n"
                ),
                {"p": product, "q": q, "n": limit},
            )
            ids = [r[0] for r in res]
            found = (
                await s.execute(select(KnowledgeChunk).where(KnowledgeChunk.id.in_(ids)))
            ).scalars()
            by_id = {c.id: c for c in found}
            rows = [by_id[i] for i in ids if i in by_id]
        else:
            allrows = (
                (await s.execute(select(KnowledgeChunk).order_by(KnowledgeChunk.id))).scalars()
            ).all()
            wanted = _stems(query)
            scored = []
            for c in allrows:
                if c.product.lower() != product.lower():
                    continue
                score = len(wanted & _stems(c.heading + " " + c.text))
                if score:
                    scored.append((score, c))
            rows = [c for _, c in sorted(scored, key=lambda x: -x[0])[:limit]]
    return [Hit(c.product, c.source, c.heading, c.text) for c in rows]
