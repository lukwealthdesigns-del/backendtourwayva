"""
Simple text chunker for RAG ingestion (Master Blueprint §33: Document
-> cleaning -> chunking -> metadata -> embeddings -> pgvector).

Deliberately simple: splits on paragraph boundaries, then greedily
packs paragraphs into chunks up to `max_chars`. No semantic chunking,
no overlap between chunks — a reasonable starting point that keeps
chunk boundaries readable, with room to get smarter (sentence-aware
splitting, sliding-window overlap) once retrieval quality on real
content shows it's needed.
"""
from __future__ import annotations

DEFAULT_MAX_CHUNK_CHARS = 1000


def chunk_text(content: str, *, max_chars: int = DEFAULT_MAX_CHUNK_CHARS) -> list[str]:
    content = content.strip()
    if not content:
        return []

    paragraphs = [p.strip() for p in content.split("\n\n") if p.strip()]
    if not paragraphs:
        paragraphs = [content]

    chunks: list[str] = []
    current = ""

    for paragraph in paragraphs:
        # A single paragraph longer than max_chars is hard-split on
        # its own rather than dropped or left oversized.
        if len(paragraph) > max_chars:
            if current:
                chunks.append(current)
                current = ""
            for i in range(0, len(paragraph), max_chars):
                chunks.append(paragraph[i : i + max_chars])
            continue

        candidate = f"{current}\n\n{paragraph}" if current else paragraph
        if len(candidate) > max_chars:
            chunks.append(current)
            current = paragraph
        else:
            current = candidate

    if current:
        chunks.append(current)

    return chunks
