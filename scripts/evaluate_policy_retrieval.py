import argparse
import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.rag.evaluation import evaluate_offline  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic offline policy retrieval benchmark.")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    args = parser.parse_args()

    cases_path = DATA_DIR / "cases.jsonl"
    policy_path = DATA_DIR / "policies.json"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    policies = json.loads(policy_path.read_text(encoding="utf-8"))["policies"]
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    payload = {
        "dataset_sha256": hashlib.sha256(cases_path.read_bytes() + policy_path.read_bytes()).hexdigest(),
        "split": args.split,
        **evaluate_offline(cases, policies),
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
