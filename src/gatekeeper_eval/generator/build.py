"""Builds eval/datasets/v1. Never imports gatekeeper.rules: labels must not be derived from the code under test.
tests/eval parses this package's AST to enforce that."""

from __future__ import annotations

import hashlib
import random
from pathlib import Path
from typing import Any

from gatekeeper.rag.chunker import chunk_document
from gatekeeper_eval.generator.audit_cases import build_audit_cases
from gatekeeper_eval.generator.base_products import build_base_products, dumps
from gatekeeper_eval.generator.invariants import check_base
from gatekeeper_eval.generator.policy_qa import QUESTIONS, UNANSWERABLE
from gatekeeper_eval.generator.proposal_cases import build_proposal_cases

ROOT = Path(__file__).resolve().parents[3]
OUT = ROOT / "eval" / "datasets" / "v2"
POLICIES = ROOT / "fixtures" / "policies"
SEED = 2026


def corpus_chunks() -> list[Any]:
    chunks = []
    for path in sorted(POLICIES.glob("*.md")):
        chunks.extend(chunk_document(path.stem, path.read_text(encoding="utf-8")))
    return chunks


def sentences(text: str) -> list[str]:
    out = []
    for line in text.split("\n"):
        if line.startswith("#"):
            continue
        for sentence in line.split(". "):
            sentence = sentence.strip().rstrip(".")
            if 25 <= len(sentence) <= 300 and len(sentence.split()) >= 5:
                out.append(sentence)
    return out


def chunk_sentence_rows() -> list[dict[str, str]]:
    return [
        {"doc_id": c.doc_id, "chunk_id": c.id, "sentence": s, "text": c.text}
        for c in corpus_chunks()
        for s in sentences(c.text)
    ]


def _split_for(item_id: str) -> str:
    return "dev" if int(hashlib.sha256(item_id.encode()).hexdigest(), 16) % 2 == 0 else "test"


def build_policy_qa(split: str) -> list[dict[str, Any]]:
    chunks = corpus_chunks()
    rows: list[dict[str, Any]] = []
    for qid, subset, question, doc_id, phrase in QUESTIONS:
        if _split_for(qid) != split:
            continue
        gold = [c.id for c in chunks if c.doc_id == doc_id and phrase in c.text]
        if len(gold) != 1:
            raise ValueError(f"{qid}: phrase must identify exactly one chunk, found {gold}")
        rows.append({"id": qid, "split": split, "subset": subset, "question": question, "gold_chunk_ids": gold})
    for qid, question in UNANSWERABLE:
        if _split_for(qid) == split:
            rows.append(
                {"id": qid, "split": split, "subset": "unanswerable", "question": question, "gold_chunk_ids": []}
            )
    return rows


def _typography_variant(sentence: str, rng: random.Random) -> str:
    choice = rng.choice(["upper", "space", "newline", "dash", "plain"])
    if choice == "upper":
        return sentence.upper()
    if choice == "space":
        return sentence.replace(" ", "  ", 2)
    if choice == "newline":
        return sentence.replace(" ", "\n", 1)
    if choice == "dash":
        return sentence.replace("-", "–")
    return sentence


def build_citation_cases(split: str, seed: int = 17) -> list[dict[str, Any]]:
    rng = random.Random(f"{seed}-{split}")
    chunks = corpus_chunks()
    rows = chunk_sentence_rows()
    rows = [r for r in rows if _split_for(r["chunk_id"] + r["sentence"]) == split]
    cases: list[dict[str, Any]] = []

    def add(
        kind: str,
        row: dict[str, str],
        quote: str,
        *,
        chunk_id: str | None = None,
        doc_id: str | None = None,
        retrieved: bool = True,
        valid: bool,
        reason: str | None = None,
    ) -> None:
        cases.append(
            {
                "id": f"{split}-{kind}-{len(cases):04d}",
                "split": split,
                "kind": kind,
                "valid": valid,
                "expected_reason": reason,
                "retrieved": retrieved,
                "citation": {
                    "doc_id": doc_id or row["doc_id"],
                    "chunk_id": chunk_id or row["chunk_id"],
                    "quote": quote,
                },
            }
        )

    picks = rng.sample(rows, min(50, len(rows)))
    for row in picks[:25]:
        add("valid_typography", row, _typography_variant(row["sentence"], rng), valid=True)
    for row in picks:
        words = row["sentence"].split()
        index = rng.randrange(1, len(words))
        kind = rng.choice(
            ["altered", "dropped", "reordered", "wrong_chunk", "not_retrieved", "doc_mismatch", "ellipsis"]
        )
        if kind == "altered":
            add(
                kind,
                row,
                " ".join([*words[:index], "zzzqx", *words[index + 1 :]]),
                valid=False,
                reason="quote_not_found",
            )
        elif kind == "dropped":
            add(kind, row, " ".join(words[:index] + words[index + 1 :]), valid=False, reason="quote_not_found")
        elif kind == "reordered":
            swapped = words[:]
            swapped[index - 1], swapped[index] = swapped[index], swapped[index - 1]
            if swapped == words:
                continue
            add(kind, row, " ".join(swapped), valid=False, reason="quote_not_found")
        elif kind == "wrong_chunk":
            others = [c for c in chunks if c.id != row["chunk_id"] and row["sentence"] not in c.text]
            other = rng.choice(others)
            add(
                kind,
                row,
                row["sentence"],
                chunk_id=other.id,
                doc_id=other.doc_id,
                valid=False,
                reason="quote_not_found",
            )
        elif kind == "not_retrieved":
            add(kind, row, row["sentence"], retrieved=False, valid=False, reason="not_retrieved")
        elif kind == "doc_mismatch":
            add(kind, row, row["sentence"], doc_id="not-" + row["doc_id"], valid=False, reason="doc_mismatch")
        else:
            add(kind, row, " ".join(words[: index + 1]) + "... " + " ".join(words[-2:]), valid=False, reason="shape")
    # A quote spanning two adjacent chunks exists in neither.
    for first, second in zip(chunks, chunks[1:], strict=False):
        if first.doc_id == second.doc_id and _split_for(first.id) == split:
            head = sentences(first.text)
            tail = sentences(second.text)
            if head and tail:
                row = {"doc_id": first.doc_id, "chunk_id": first.id, "sentence": head[-1], "text": first.text}
                add("spans_chunks", row, head[-1] + ". " + tail[0], valid=False, reason="quote_not_found")
    return cases


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    # Explicit LF and sorted keys, so the file hash is the same on Windows and Linux.
    with path.open("w", encoding="utf-8", newline="\n") as handle:
        for row in rows:
            handle.write(dumps(row) + "\n")


def build_all() -> dict[str, int]:
    OUT.mkdir(parents=True, exist_ok=True)
    bases = build_base_products()
    for base in bases:
        check_base(base)
    sentence_rows = chunk_sentence_rows()
    counts: dict[str, int] = {}
    for split in ("dev", "test"):
        for name, rows in (
            (f"audit_{split}.jsonl", build_audit_cases(bases, split)),
            (f"proposals_{split}.jsonl", build_proposal_cases(bases, sentence_rows, split)),
            (f"policy_qa_{split}.jsonl", build_policy_qa(split)),
            (f"citations_{split}.jsonl", build_citation_cases(split)),
        ):
            write_jsonl(OUT / name, rows)
            counts[name] = len(rows)
    return counts


if __name__ == "__main__":
    for file_name, count in build_all().items():
        print(f"{file_name}: {count}")
