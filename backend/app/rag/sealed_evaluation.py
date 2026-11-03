"""Hash-first evaluation for independently authored retrieval challenges."""
from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from app.rag.evaluation import _bm25_scores
from app.rag.policy_chunks import policy_clauses


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def seal_files(queries_path: Path, answer_key_path: Path, policies_path: Path, scorer_path: Path) -> dict[str, Any]:
    """Hash opaque input files without parsing or joining the answer key."""
    return {
        "schema_version": 1,
        "queries_file": queries_path.name,
        "queries_sha256": _sha256(queries_path),
        "answer_key_file": answer_key_path.name,
        "answer_key_sha256": _sha256(answer_key_path),
        "policies_file": policies_path.name,
        "policies_sha256": _sha256(policies_path),
        "scorer_file": scorer_path.name,
        "scorer_sha256": _sha256(scorer_path),
    }


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"{path.name}:{line_number} is not valid JSON: {error.msg}") from error
        if not isinstance(record, dict):
            raise ValueError(f"{path.name}:{line_number} must contain a JSON object.")
        records.append(record)
    return records


def _unique_by_case_id(records: list[dict[str, Any]], source_name: str) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    for row in records:
        case_id = row.get("case_id")
        if not isinstance(case_id, str) or not case_id.strip():
            raise ValueError(f"Every record in {source_name} needs a non-empty case_id.")
        if case_id in indexed:
            raise ValueError(f"Duplicate case_id {case_id!r} in {source_name}.")
        indexed[case_id] = row
    return indexed


def _rates(rows: list[dict[str, Any]]) -> dict[str, Any]:
    relevant = [row for row in rows if row["policy_relevant"]]
    irrelevant = [row for row in rows if not row["policy_relevant"]]
    cited_relevant = [row for row in relevant if row["cited"]]
    return {
        "cases": len(rows),
        "expected_action_accuracy": sum(row["correct"] for row in rows) / len(rows) if rows else None,
        "relevant_citation_coverage": sum(row["cited"] for row in relevant) / len(relevant) if relevant else None,
        "relevant_citation_accuracy": sum(row["correct_citation"] for row in cited_relevant) / len(cited_relevant) if cited_relevant else None,
        "relevant_abstention_rate": sum(not row["cited"] for row in relevant) / len(relevant) if relevant else None,
        "irrelevant_false_citation_rate": sum(row["cited"] for row in irrelevant) / len(irrelevant) if irrelevant else None,
        "citation_source_integrity": all(row["source_integrity"] for row in rows if row["cited"]),
    }


def evaluate_sealed_files(
    queries_path: Path,
    answer_key_path: Path,
    policies_path: Path,
    scorer_path: Path,
    seal_path: Path,
) -> dict[str, Any]:
    """Verify pre-scoring hashes, then join labels and score with frozen BM25."""
    seal = json.loads(seal_path.read_text(encoding="utf-8"))
    current_seal = seal_files(queries_path, answer_key_path, policies_path, scorer_path)
    if seal != current_seal:
        changed = [key for key in current_seal if seal.get(key) != current_seal[key]]
        raise ValueError(f"Inputs differ from the pre-scoring seal: {', '.join(changed)}")

    queries = _unique_by_case_id(_read_jsonl(queries_path), queries_path.name)
    answer_key = _unique_by_case_id(_read_jsonl(answer_key_path), answer_key_path.name)
    if not queries:
        raise ValueError("The challenge set must contain at least one query.")
    if queries.keys() != answer_key.keys():
        missing = sorted(queries.keys() - answer_key.keys())
        extra = sorted(answer_key.keys() - queries.keys())
        raise ValueError(f"Query/answer case IDs differ (missing labels: {missing}; extra labels: {extra}).")

    policy_doc = json.loads(policies_path.read_text(encoding="utf-8"))
    policies_by_key = {
        (policy["policy_id"], policy["version"]): policy
        for policy in policy_doc["policies"]
    }
    case_results = []
    for case_id, query_row in queries.items():
        if set(query_row) != {"case_id", "query"}:
            raise ValueError(f"{case_id}: query rows must contain only case_id and query.")
        query = query_row["query"]
        if not isinstance(query, str) or not query.strip():
            raise ValueError(f"{case_id}: query must be non-empty text.")

        label = answer_key[case_id]
        required = {"case_id", "policy_relevant", "expected_action", "target_policy_id", "target_version"}
        if not required.issubset(label):
            raise ValueError(f"{case_id}: answer row is missing {sorted(required - set(label))}.")
        policy_relevant = label["policy_relevant"]
        expected_action = label["expected_action"]
        if not isinstance(policy_relevant, bool) or expected_action not in {"cite", "abstain"}:
            raise ValueError(f"{case_id}: policy_relevant must be boolean and expected_action cite/abstain.")
        if not policy_relevant and expected_action != "abstain":
            raise ValueError(f"{case_id}: an irrelevant query must have expected_action='abstain'.")
        policy_key = (label["target_policy_id"], label["target_version"])
        policy = policies_by_key.get(policy_key)
        if policy is None:
            raise ValueError(f"{case_id}: unknown target policy/version {policy_key!r}.")

        clauses = policy_clauses(policy["policy_id"], policy["version"], policy["source"])
        clause_sources = {str(clause["chunk_id"]): str(clause["text"]) for clause in clauses}
        scores = _bm25_scores(query, sorted(clause_sources), clause_sources, include_cjk=True)
        ranked = sorted(scores, key=lambda chunk_id: (-scores[chunk_id], chunk_id))
        selected_id = ranked[0] if ranked and scores[ranked[0]] > 0 else None
        selected = next((clause for clause in clauses if str(clause["chunk_id"]) == selected_id), None)
        selected_index = next((i for i, clause in enumerate(clauses) if str(clause["chunk_id"]) == selected_id), None)
        cited = selected is not None
        correct_citation = False
        if expected_action == "cite":
            expected_index = label.get("expected_citation_index")
            if not isinstance(expected_index, int) or not 0 <= expected_index < len(clauses):
                raise ValueError(f"{case_id}: cite labels need an in-range expected_citation_index.")
            correct_citation = cited and selected_index == expected_index
            correct = correct_citation
        else:
            if "expected_citation_index" in label:
                raise ValueError(f"{case_id}: abstain labels must not have expected_citation_index.")
            correct = not cited

        case_results.append({
            "case_id": case_id,
            "policy_relevant": policy_relevant,
            "expected_action": expected_action,
            "intent_category": label.get("intent_category", "unspecified"),
            "language": label.get("language", "unspecified"),
            "cited": cited,
            "correct": correct,
            "correct_citation": correct_citation,
            "top_score": round(scores[selected_id], 6) if selected_id else 0.0,
            "citation_ref": selected["citation_ref"] if selected else None,
            "source_integrity": selected["text"] in policy["source"] if selected else None,
        })

    groups: dict[str, dict[str, list[dict[str, Any]]]] = {
        "language": defaultdict(list),
        "intent_category": defaultdict(list),
    }
    for row in case_results:
        groups["language"][row["language"]].append(row)
        groups["intent_category"][row["intent_category"]].append(row)
    return {
        "report_type": "sealed_policy_retrieval_challenge",
        "offline_challenge_only": True,
        "real_model_evaluation": False,
        "seal": seal,
        "overall": _rates(case_results),
        "by_language": {name: _rates(rows) for name, rows in sorted(groups["language"].items())},
        "by_intent_category": {name: _rates(rows) for name, rows in sorted(groups["intent_category"].items())},
        "cases": case_results,
    }
