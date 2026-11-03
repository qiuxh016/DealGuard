"""Evaluate frozen lexical retrieval against a separate bilingual challenge set."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
from time import perf_counter

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.rag.evaluation import _bilingual_query_matches, _bm25_scores  # noqa: E402
from app.rag.policy_chunks import policy_clauses  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_rag_safety"
CASE_PATH = DATA_DIR / "crosslingual_challenge_cases.jsonl"
POLICY_PATH = DATA_DIR / "policies.json"
SCORER_PATH = ROOT / "backend" / "app" / "rag" / "evaluation.py"


def evaluate() -> dict:
    policies = json.loads(POLICY_PATH.read_text(encoding="utf-8"))["policies"]
    policies_by_id = {policy["policy_id"]: policy for policy in policies}
    cases = [
        json.loads(line)
        for line in CASE_PATH.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    results = []
    started = perf_counter()

    for case in cases:
        policy = policies_by_id[case["target_policy_id"]]
        clauses = policy_clauses(policy["policy_id"], policy["version"], policy["source"])
        clause_sources = {str(clause["chunk_id"]): str(clause["text"]) for clause in clauses}
        scores = _bm25_scores(case["query"], sorted(clause_sources), clause_sources, include_cjk=True)
        ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        top_score = scores[ranked[0]] if ranked else 0.0
        selected_id = ranked[0] if top_score > 0 else None
        selected_clause = next(
            (clause for clause in clauses if str(clause["chunk_id"]) == selected_id),
            None,
        )
        selected_index = next(
            (index for index, clause in enumerate(clauses) if str(clause["chunk_id"]) == selected_id),
            None,
        )
        cited = selected_clause is not None
        expected_cite = case["expected"] == "cite"
        correct = (
            cited
            and case["target_policy_id"] == policy["policy_id"]
            and selected_index == case.get("expected_citation_index")
        ) if expected_cite else not cited
        results.append({
            "case_id": case["case_id"],
            "policy_relevant": case["policy_relevant"],
            "expected": case["expected"],
            "cited": cited,
            "correct": correct,
            "top_score": round(top_score, 6),
            "citation_ref": selected_clause["citation_ref"] if selected_clause else None,
            "source_integrity": selected_clause["text"] in policy["source"] if selected_clause else None,
            "query_expansion_terms": _bilingual_query_matches(case["query"]),
        })

    relevant = [result for result in results if result["policy_relevant"]]
    irrelevant = [result for result in results if not result["policy_relevant"]]
    cited_relevant = [result for result in relevant if result["cited"]]
    output = {
        "offline_challenge_only": True,
        "real_model_evaluation": False,
        "case_count": len(results),
        "relevant_case_count": len(relevant),
        "irrelevant_case_count": len(irrelevant),
        "relevant_citation_coverage": sum(result["cited"] for result in relevant) / len(relevant) if relevant else None,
        "relevant_citation_accuracy": sum(result["correct"] for result in cited_relevant) / len(cited_relevant) if cited_relevant else None,
        "relevant_abstention_rate": sum(not result["cited"] for result in relevant) / len(relevant) if relevant else None,
        "irrelevant_false_citation_rate": sum(result["cited"] for result in irrelevant) / len(irrelevant) if irrelevant else None,
        "citation_source_integrity": all(result["source_integrity"] for result in results if result["cited"]),
        "dataset_sha256": hashlib.sha256(CASE_PATH.read_bytes()).hexdigest(),
        "frozen_scorer_sha256": hashlib.sha256(SCORER_PATH.read_bytes()).hexdigest(),
        "elapsed_ms": round((perf_counter() - started) * 1000, 3),
        "cases": results,
    }
    return output


if __name__ == "__main__":
    print(json.dumps(evaluate(), ensure_ascii=False, indent=2, sort_keys=True))
