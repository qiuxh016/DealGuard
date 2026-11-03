import argparse
from collections import defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import statistics
import sys
from time import perf_counter
from types import SimpleNamespace
from decimal import Decimal


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.rag.evaluation import _bm25_rank  # noqa: E402
from app.rag.policy_chunks import policy_clauses  # noqa: E402
from app.rag.policy_graph import policy_conflict_graph  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"
SAFETY_DIR = ROOT / "data" / "policy_rag_safety"
AS_OF = datetime(2026, 11, 3, tzinfo=timezone.utc)


def _language(text: str) -> str:
    return "zh" if re.search(r"[\u4e00-\u9fff]", text) else "en"


def _source_clauses(policy: dict) -> list[dict]:
    return policy_clauses(policy["policy_id"], policy["version"], policy["source"])


def _row(policy: dict, suffix: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        id=policy["policy_id"] + suffix,
        policy_id=policy["policy_id"],
        version=policy["version"],
        tenant_id=policy.get("tenant_id", "tenant-a"),
        action_type=policy["action_type"],
        customer_tier=policy["customer_tier"],
        policy_scope=policy["policy_scope"],
        scope_product_id=policy.get("scope_product_id"),
        status=policy.get("status", "active"),
        effective_from=datetime.fromisoformat(policy.get("effective_from", "2020-01-01T00:00:00+00:00").replace("Z", "+00:00")),
        effective_to=datetime.fromisoformat(policy["effective_to"].replace("Z", "+00:00")) if policy.get("effective_to") else None,
        max_discount_pct=Decimal(str(policy["max_discount_pct"])) if policy.get("max_discount_pct") is not None else None,
        review_max_discount_pct=Decimal(str(policy["review_max_discount_pct"])) if policy.get("review_max_discount_pct") is not None else None,
        exception_approval_allowed=policy.get("exception_approval_allowed", False),
    )


def _decoys(base_policies: list[dict], case: dict) -> tuple[list[dict], dict[str, str]]:
    rows = []
    categories = {}
    for policy in base_policies:
        wrong_scope = next(scope for scope in ("general", "bulk_order", "loyalty", "early_payment", "seasonal_campaign", "product_specific") if scope not in {case["scope"], "general"})
        variants = (
            ("expired", {"effective_to": "2025-01-01T00:00:00Z"}),
            ("cross_tenant", {"tenant_id": "tenant-b"}),
            ("wrong_scope", {"policy_scope": wrong_scope, "scope_product_id": "P-999" if wrong_scope == "product_specific" else None}),
            ("reference_only", {"action_type": "reference_only"}),
        )
        for category, override in variants:
            clone = {**policy, **override, "policy_id": f"DECOY-{category}-{policy['policy_id']}"}
            rows.append(clone)
            categories[clone["policy_id"]] = category
    if case.get("scenario") == "overlap_conflict":
        target = next(policy for policy in base_policies if policy["policy_id"] == "EVAL-BULK")
        rows.append({**target, "policy_id": "SAFETY-BULK-CONFLICT", "version": "v1", "max_discount_pct": "4", "effective_from": "2026-11-01T00:00:00Z"})
    return rows, categories


def _eligible(case: dict, policies: list[dict]) -> list[SimpleNamespace]:
    moment = datetime.fromisoformat(case.get("as_of", AS_OF.isoformat()).replace("Z", "+00:00"))
    all_rows = []
    for policy in policies:
        row = _row(policy)
        start = row.effective_from.replace(tzinfo=timezone.utc) if row.effective_from.tzinfo is None else row.effective_from
        end = row.effective_to.replace(tzinfo=timezone.utc) if row.effective_to and row.effective_to.tzinfo is None else row.effective_to
        if (
            row.tenant_id == case.get("tenant_id", "tenant-a")
            and row.action_type == "quote_discount"
            and row.status == "active"
            and row.customer_tier == case["customer_tier"]
            and start <= moment
            and (end is None or moment < end)
        ):
            all_rows.append(row)
    if case["scope"] == "general":
        return [row for row in all_rows if row.policy_scope == "general"]
    scoped = [
        row for row in all_rows
        if row.policy_scope == case["scope"]
        and (row.policy_scope != "product_specific" or row.scope_product_id == case.get("product_id"))
    ]
    return scoped or [row for row in all_rows if row.policy_scope == "general"]


def _case_set(base_cases: list[dict], boundaries: list[dict], policies: list[dict]) -> list[dict]:
    cases = []
    for original in base_cases:
        target = original["expected_policy_id"]
        cases.append({
            "case_id": f"safety-{original['case_id']}",
            "split": original["split"],
            "query": original["query"],
            "scope": next(policy["policy_scope"] for policy in policies if policy["policy_id"] == target),
            "product_id": "P-100",
            "customer_tier": original["customer_tier"],
            "expected_candidate_ids": [target],
            "expected_policy_id": target,
            "expected_decision": original.get("expected_decision"),
            "requested_discount_pct": original.get("requested_discount_pct"),
            "expected_conflict": False,
            "expected_citation_indices": [0],
            "scenario": "decoys",
        })
    cases.extend(boundaries)
    return cases


def _ratio(numerator: int, denominator: int) -> float | None:
    return round(numerator / denominator, 4) if denominator else None


def _summarize(results: list[dict], citation_field: str = "citation_correct") -> dict:
    total = len(results)
    accepted = [item for item in results if item["selected_policy_id"] is not None]
    eligible_expected = [item for item in results if item["expected_policy_id"] is not None]
    conflict_actual = [item for item in results if item["actual_conflict"]]
    conflict_expected = [item for item in results if item["expected_conflict"]]
    expected_abstentions = [item for item in results if item["expected_policy_id"] is None]
    false_inclusions = sum(len(item["unexpected_candidate_ids"]) for item in results)
    return {
        "cases": total,
        "candidate_filter_precision": _ratio(sum(item["expected_candidate_ids"] == item["actual_candidate_ids"] for item in results), total),
        "unexpected_candidate_inclusions": false_inclusions,
        "decoy_leaks_by_category": {
            category: sum(category in item["included_decoy_categories"] for item in results)
            for category in ("expired", "cross_tenant", "wrong_scope", "reference_only")
        },
        "conflict_precision": _ratio(sum(item["actual_conflict"] and item["expected_conflict"] for item in results), len(conflict_actual)),
        "conflict_recall": _ratio(sum(item["actual_conflict"] and item["expected_conflict"] for item in results), len(conflict_expected)),
        "conflict_false_negatives": [item["case_id"] for item in conflict_expected if not item["actual_conflict"]],
        "decision_coverage": _ratio(len(accepted), total),
        "selective_policy_accuracy": _ratio(sum(item["selected_policy_id"] == item["expected_policy_id"] for item in accepted), len(accepted)),
        "expected_abstention_recall": _ratio(sum(item["selected_policy_id"] is None for item in expected_abstentions), len(expected_abstentions)),
        "citation_accuracy_at_1": _ratio(sum(item[citation_field] for item in eligible_expected), len(eligible_expected)),
        "citation_source_integrity": _ratio(sum(item["citation_is_source_substring"] for item in results if item["citation"] is not None), sum(item["citation"] is not None for item in results)),
        "citation_failures": [item["case_id"] for item in eligible_expected if not item[citation_field]],
        "offline_latency_ms": {
            "p50": round(statistics.median([item["latency_ms"] for item in results]), 3) if results else None,
            "p95": round(sorted(item["latency_ms"] for item in results)[max(0, math.ceil(len(results) * 0.95) - 1)], 3) if results else None,
            "note": "Python offline harness only; excludes model load and provider/runtime overhead.",
        },
    }


def evaluate(cases: list[dict], base_policies: list[dict], embedding_model: str | None = None) -> dict:
    embeddings = None
    model_load_seconds = None
    document_embedding_seconds = None
    dense_vectors: dict[str, list[list[float]]] = {}
    if embedding_model:
        from app.agents.workflow import _LocalPolicyEmbeddings

        model_started = perf_counter()
        embeddings = _LocalPolicyEmbeddings(embedding_model)
        model_load_seconds = round(perf_counter() - model_started, 3)
        document_started = perf_counter()
        for policy in base_policies:
            clauses = _source_clauses(policy)
            dense_vectors[policy["policy_id"]] = embeddings.embed_documents([str(clause["text"]) for clause in clauses])
        document_embedding_seconds = round(perf_counter() - document_started, 3)
    by_language: dict[str, list[dict]] = defaultdict(list)
    results = []
    for case in cases:
        started = perf_counter()
        retrieval_query = case["query"]
        if case.get("scenario") in {"multi_candidate", "multi_disagreement"}:
            retrieval_query = f"P-100 100 units at 1% discount. {retrieval_query}"
        decoys, decoy_categories = _decoys(base_policies, case)
        registry = [
            {**policy, "status": "active" if case.get("scenario") in {"multi_candidate", "multi_disagreement"} and policy["policy_id"] == "EVAL-BULK-ALT" else policy.get("status", "active")}
            for policy in base_policies
        ] + decoys
        expected_ids = sorted(case["expected_candidate_ids"])
        eligible = _eligible(case, registry)
        actual_ids = sorted(row.policy_id for row in eligible)
        graph = policy_conflict_graph(eligible)
        actual_conflict = bool(graph["edges"])
        selected_policy_id = None
        citation = None
        citation_ref = None
        policy_source_hash = None
        citation_correct = case["expected_policy_id"] is None
        citation_correct_bm25 = citation_correct
        citation_correct_dense = citation_correct
        citation_is_source_substring = True
        bm25_policy_id = None
        dense_policy_id = None
        if eligible and not actual_conflict:
            row_by_policy_id = {policy["policy_id"]: policy for policy in registry if policy["policy_id"] in actual_ids}
            clause_by_id = {}
            for row in eligible:
                source_policy = row_by_policy_id[row.policy_id]
                for clause in _source_clauses(source_policy):
                    clause_by_id[str(clause["chunk_id"])] = (row, clause)
            clause_ids = sorted(clause_by_id)
            clause_sources = {chunk_id: str(clause_by_id[chunk_id][1]["text"]) for chunk_id in clause_ids}
            ranking = _bm25_rank(retrieval_query, clause_ids, clause_sources, include_cjk=True)
            lexical_policy_ids = list(dict.fromkeys(clause_by_id[chunk_id][0].policy_id for chunk_id in ranking))
            bm25_policy_id = lexical_policy_ids[0] if lexical_policy_ids else None
            expected_indices = case.get("expected_citation_indices", [0])
            expected_policy_id = case["expected_policy_id"]
            if expected_policy_id and ranking and clause_by_id[ranking[0]][0].policy_id == expected_policy_id:
                expected_clauses = _source_clauses(row_by_policy_id[expected_policy_id])
                expected_clause_index = next(index for index, clause in enumerate(expected_clauses) if str(clause["chunk_id"]) == ranking[0])
                citation_correct_bm25 = expected_clause_index in expected_indices
            dense_top_id = None
            if embeddings:
                query_vector = embeddings.embed_query(retrieval_query)
                document_vectors = {
                    chunk_id: dense_vectors[clause_by_id[chunk_id][0].policy_id][
                        next(index for index, clause in enumerate(_source_clauses(row_by_policy_id[clause_by_id[chunk_id][0].policy_id])) if str(clause["chunk_id"]) == chunk_id)
                    ]
                    for chunk_id in clause_ids
                }
                vector_scores = {
                    chunk_id: sum(left * right for left, right in zip(query_vector, vector, strict=True))
                    for chunk_id, vector in document_vectors.items()
                }
                best_clause = {
                    row.policy_id: max(
                        (clause for clause_id, (owner, clause) in clause_by_id.items() if owner.policy_id == row.policy_id),
                        key=lambda clause: (round(vector_scores[str(clause["chunk_id"])], 8), str(clause["chunk_id"])),
                    )
                    for row in eligible
                }
                vector_policy_rank = sorted(
                    best_clause,
                    key=lambda policy_id: (-round(vector_scores[str(best_clause[policy_id]["chunk_id"])], 8), policy_id),
                )
                dense_policy_id = vector_policy_rank[0] if vector_policy_rank else None
                dense_top_id = str(best_clause[dense_policy_id]["chunk_id"]) if dense_policy_id else None
            if len(eligible) == 1:
                selected_policy_id = eligible[0].policy_id
            elif bm25_policy_id and bm25_policy_id == dense_policy_id:
                selected_policy_id = dense_policy_id
            if selected_policy_id:
                chosen_clause = clause_by_id[dense_top_id][1] if len(eligible) > 1 else clause_by_id[str(best_clause[selected_policy_id]["chunk_id"])][1] if embeddings else clause_by_id[ranking[0]][1]
                source_policy = row_by_policy_id[selected_policy_id]
                citation = str(chosen_clause["text"])
                citation_ref = str(chosen_clause["citation_ref"])
                policy_source_hash = hashlib.sha256(source_policy["source"].encode("utf-8")).hexdigest()
                actual_index = next(index for index, clause in enumerate(_source_clauses(source_policy)) if str(clause["chunk_id"]) == str(chosen_clause["chunk_id"]))
                citation_correct = selected_policy_id == case["expected_policy_id"] and actual_index in expected_indices
                citation_correct_dense = citation_correct
                citation_is_source_substring = citation in source_policy["source"]
        included_decoy_categories = sorted({decoy_categories[pid] for pid in actual_ids if pid in decoy_categories})
        if actual_conflict or not eligible:
            abstain_reason = "POLICY_CONFLICT" if actual_conflict else "NO_APPLICABLE_POLICY"
        elif len(eligible) == 1:
            selected_policy_id = eligible[0].policy_id
            abstain_reason = None
        elif embeddings and bm25_policy_id and bm25_policy_id == dense_policy_id:
            selected_policy_id = dense_policy_id
            abstain_reason = None
        else:
            abstain_reason = "POLICY_RETRIEVAL_AMBIGUOUS" if embeddings else "MULTI_CANDIDATE_REQUIRES_DENSE_SELECTOR"
        expected_policy_id = case["expected_policy_id"]
        result = {
            "case_id": case["case_id"],
            "split": case.get("split", "safety"),
            "language": _language(case["query"]),
            "expected_candidate_ids": expected_ids,
            "actual_candidate_ids": actual_ids,
            "unexpected_candidate_ids": sorted(set(actual_ids) - set(expected_ids)),
            "missing_candidate_ids": sorted(set(expected_ids) - set(actual_ids)),
            "included_decoy_categories": included_decoy_categories,
            "expected_conflict": case["expected_conflict"],
            "actual_conflict": actual_conflict,
            "conflict_edges": graph["edges"],
            "expected_policy_id": expected_policy_id,
            "selected_policy_id": selected_policy_id,
            "abstain_reason": abstain_reason,
            "bm25_top_policy_id": bm25_policy_id,
            "dense_top_policy_id": dense_policy_id,
            "citation": citation,
            "citation_ref": citation_ref,
            "policy_source_hash": policy_source_hash,
            "citation_correct": citation_correct,
            "citation_correct_bm25": citation_correct_bm25,
            "citation_correct_dense": citation_correct_dense,
            "citation_is_source_substring": citation_is_source_substring,
            "latency_ms": round((perf_counter() - started) * 1000, 3),
        }
        results.append(result)
        by_language[result["language"]].append(result)
    methods = {
        "bm25_clause_baseline": _summarize(results, "citation_correct_bm25"),
        "by_language_bm25": {language: _summarize(items, "citation_correct_bm25") for language, items in sorted(by_language.items())},
        "by_split_bm25": {split: _summarize([item for item in results if item["split"] == split], "citation_correct_bm25") for split in sorted({item["split"] for item in results})},
    }
    if embedding_model:
        methods["local_embedding_clause_ranker"] = _summarize(results, "citation_correct_dense")
        methods["by_language_local_embedding"] = {language: _summarize(items, "citation_correct_dense") for language, items in sorted(by_language.items())}
        methods["by_split_local_embedding"] = {split: _summarize([item for item in results if item["split"] == split], "citation_correct_dense") for split in sorted({item["split"] for item in results})}
    return {
        "offline_safety_harness_only": True,
        "real_model_evaluation": bool(embedding_model),
        "corpus": "Versioned Markdown policies with headings and paragraph sections; fixed evaluation text, not production customer policies.",
        "embedding_model": Path(embedding_model).name if embedding_model else None,
        "model_load_seconds": model_load_seconds,
        "document_embedding_seconds": document_embedding_seconds,
        "methods": methods,
        "cases": results,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate policy RAG safety gates and clause citations offline.")
    parser.add_argument("--with-local-embedding", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    policy_path = SAFETY_DIR / "policies.json"
    retrieval_path = DATA_DIR / "cases_v2.jsonl"
    boundary_path = SAFETY_DIR / "boundary_cases.jsonl"
    policies = json.loads(policy_path.read_text(encoding="utf-8"))["policies"]
    retrieval_cases = [json.loads(line) for line in retrieval_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    boundaries = [json.loads(line) for line in boundary_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    cases = _case_set(retrieval_cases, boundaries, policies)
    model_path = None
    if args.with_local_embedding:
        from app.core.config import settings

        if not settings.local_embedding_model_path:
            parser.error("Set LOCAL_EMBEDDING_MODEL_PATH before evaluating local embeddings.")
        model_path = settings.local_embedding_model_path
    corpus_digest = hashlib.sha256(policy_path.read_bytes() + retrieval_path.read_bytes() + boundary_path.read_bytes()).hexdigest()
    package_versions = {}
    if model_path:
        package_versions = {name: importlib.metadata.version(name) for name in ("sentence-transformers", "torch", "transformers")}
    payload = {
        "dataset_sha256": corpus_digest,
        "dataset": "retrieval_v2_plus_policy_rag_safety_boundaries",
        "case_count": len(cases),
        "packages": package_versions,
        **evaluate(cases, policies, model_path),
    }
    output = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(output + "\n", encoding="utf-8")
    print(output)


if __name__ == "__main__":
    main()
