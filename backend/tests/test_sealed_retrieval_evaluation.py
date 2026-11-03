import json
from pathlib import Path

import pytest

from app.rag.sealed_evaluation import evaluate_sealed_files, seal_files


SCORER = Path(__file__).resolve().parents[1] / "app" / "rag" / "evaluation.py"


def _jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")


def _inputs(tmp_path: Path):
    queries = tmp_path / "queries.jsonl"
    labels = tmp_path / "answer_key.jsonl"
    policies = tmp_path / "policies.json"
    seal = tmp_path / "seal.json"
    _jsonl(queries, [
        {"case_id": "cite", "query": "wholesale volume discount"},
        {"case_id": "abstain", "query": "warehouse opening hours"},
    ])
    _jsonl(labels, [
        {"case_id": "cite", "policy_relevant": True, "expected_action": "cite", "target_policy_id": "P-TEST", "target_version": "v1", "expected_citation_index": 0, "intent_category": "direct", "language": "en"},
        {"case_id": "abstain", "policy_relevant": False, "expected_action": "abstain", "target_policy_id": "P-TEST", "target_version": "v1", "intent_category": "near-topic", "language": "en"},
    ])
    policies.write_text(json.dumps({"policies": [{
        "policy_id": "P-TEST", "version": "v1",
        "source": "# Bulk Discount\n\nEligible VIP wholesale customers may receive a volume discount.\n",
    }]}), encoding="utf-8")
    seal.write_text(json.dumps(seal_files(queries, labels, policies, SCORER)), encoding="utf-8")
    return queries, labels, policies, seal


def test_sealed_evaluator_scores_split_files_and_reports_groups(tmp_path):
    queries, labels, policies, seal = _inputs(tmp_path)
    report = evaluate_sealed_files(queries, labels, policies, SCORER, seal)

    assert report["overall"]["cases"] == 2
    assert report["overall"]["relevant_citation_coverage"] == 1
    assert report["overall"]["irrelevant_false_citation_rate"] == 0
    assert report["by_intent_category"]["direct"]["relevant_citation_accuracy"] == 1
    assert report["cases"][0]["citation_ref"].startswith("P-TEST@v1#L")


def test_sealed_evaluator_rejects_any_file_change_after_sealing(tmp_path):
    queries, labels, policies, seal = _inputs(tmp_path)
    queries.write_text(queries.read_text(encoding="utf-8").replace("wholesale", "retail"), encoding="utf-8")

    with pytest.raises(ValueError, match="queries_sha256"):
        evaluate_sealed_files(queries, labels, policies, SCORER, seal)


def test_sealed_evaluator_rejects_query_answer_id_mismatch(tmp_path):
    queries, labels, policies, _ = _inputs(tmp_path)
    _jsonl(labels, [{"case_id": "other", "policy_relevant": False, "expected_action": "abstain", "target_policy_id": "P-TEST", "target_version": "v1"}])
    seal = tmp_path / "seal.json"
    seal.write_text(json.dumps(seal_files(queries, labels, policies, SCORER)), encoding="utf-8")

    with pytest.raises(ValueError, match="case IDs differ"):
        evaluate_sealed_files(queries, labels, policies, SCORER, seal)
