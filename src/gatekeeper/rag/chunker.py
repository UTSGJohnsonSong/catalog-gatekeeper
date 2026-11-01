"""Deterministic chunker: split on headings then paragraphs, merge to a target size, no overlap."""

from __future__ import annotations

import re
from dataclasses import dataclass

TARGET_MIN = 400
TARGET_MAX = 700


@dataclass(frozen=True)
class Chunk:
    id: str
    doc_id: str
    ordinal: int
    text: str


_HEADING = re.compile(r"(?m)^(?=#{1,6} )")


def chunk_document(doc_id: str, body: str) -> list[Chunk]:
    body = body.replace("\r\n", "\n").strip()
    pieces: list[str] = []
    for section in _HEADING.split(body):
        section = section.strip()
        if not section:
            continue
        buffer = ""
        for para in re.split(r"\n\s*\n", section):
            para = para.strip()
            if not para:
                continue
            if buffer and len(buffer) + len(para) + 2 > TARGET_MAX:
                pieces.append(buffer)
                buffer = para
            else:
                buffer = f"{buffer}\n\n{para}" if buffer else para
        if buffer:
            pieces.append(buffer)
    merged: list[str] = []
    for piece in pieces:
        if merged and len(merged[-1]) < TARGET_MIN and len(merged[-1]) + len(piece) + 2 <= TARGET_MAX:
            merged[-1] = f"{merged[-1]}\n\n{piece}"
        else:
            merged.append(piece)
    return [Chunk(f"{doc_id}#{i:03d}", doc_id, i, text) for i, text in enumerate(merged)]
