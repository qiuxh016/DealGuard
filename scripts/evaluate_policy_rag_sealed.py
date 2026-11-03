"""Seal separate challenge queries/labels, then evaluate against frozen BM25."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.rag.sealed_evaluation import evaluate_sealed_files, seal_files  # noqa: E402


def _paths(args: argparse.Namespace) -> tuple[Path, Path, Path, Path]:
    return args.queries.resolve(), args.answer_key.resolve(), args.policies.resolve(), (ROOT / "backend/app/rag/evaluation.py")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("seal", "evaluate"):
        command = commands.add_parser(name)
        command.add_argument("--queries", type=Path, required=True)
        command.add_argument("--answer-key", type=Path, required=True)
        command.add_argument("--policies", type=Path, default=ROOT / "data/policy_rag_safety/policies.json")
        command.add_argument("--output", type=Path, required=True)
        if name == "evaluate":
            command.add_argument("--seal", type=Path, required=True)
    args = parser.parse_args()
    queries, answer_key, policies, scorer = _paths(args)
    if args.command == "seal":
        result = seal_files(queries, answer_key, policies, scorer)
    else:
        result = evaluate_sealed_files(queries, answer_key, policies, scorer, args.seal.resolve())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"command": args.command, "output": str(args.output), "hashes": result.get("seal", result)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
