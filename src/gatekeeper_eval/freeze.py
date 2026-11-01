"""Freezes the test split: changing any test file then fails CI, and a change must become datasets/v2."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from gatekeeper_eval.generator.build import OUT, SEED

GENERATOR_DIR = Path(__file__).parent / "generator"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_files(directory: Path = OUT) -> list[Path]:
    return sorted(directory.glob("*_test.jsonl"))


def combined_test_hash(directory: Path = OUT) -> str:
    return hashlib.sha256("".join(_sha(p) for p in test_files(directory)).encode()).hexdigest()


def generator_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(GENERATOR_DIR.glob("*.py")):
        digest.update(path.read_bytes().replace(b"\r\n", b"\n"))
    return digest.hexdigest()


def freeze(directory: Path = OUT) -> str:
    manifest = {
        "seed": SEED,
        "generator_sha256": generator_hash(),
        "files": {
            p.name: {"sha256": _sha(p), "rows": sum(1 for _ in p.open(encoding="utf-8"))}
            for p in sorted(directory.glob("*.jsonl"))
        },
    }
    (directory / "MANIFEST.json").write_text(
        json.dumps(manifest, indent=1, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
    )
    value = combined_test_hash(directory)
    (directory / "FROZEN_TEST_SHA256").write_text(value + "\n", encoding="utf-8", newline="\n")
    return value


def is_frozen_intact(directory: Path = OUT) -> bool:
    path = directory / "FROZEN_TEST_SHA256"
    return path.exists() and path.read_text(encoding="utf-8").strip() == combined_test_hash(directory)
