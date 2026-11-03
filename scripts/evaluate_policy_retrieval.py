import argparse
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
from time import perf_counter


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.workflow import _LocalPolicyEmbeddings  # noqa: E402
from app.core.config import settings  # noqa: E402
from app.rag.evaluation import _bm25_rank, _metrics, _rrf_rank, evaluate_offline  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"


def evaluate_local_model(cases: list[dict], policies: list[dict], model_path: str) -> dict:
    load_started = perf_counter()
    embeddings = _LocalPolicyEmbeddings(model_path)
    load_seconds = perf_counter() - load_started

    policy_ids = [policy["policy_id"] for policy in policies]
    sources = [policy["source"] for policy in policies]
    document_started = perf_counter()
    document_vectors = dict(zip(policy_ids, embeddings.embed_documents(sources), strict=True))
    document_seconds = perf_counter() - document_started

    rankings = []
    hybrid_rankings = []
    bm25_rankings = []
    rankings_by_case_id = {}
    hybrid_rankings_by_case_id = {}
    bm25_rankings_by_case_id = {}
    top_results = []
    hybrid_top_results = []
    query_seconds = 0.0
    for case in cases:
        query_started = perf_counter()
        query_vector = embeddings.embed_query(case["query"])
        query_seconds += perf_counter() - query_started
        scores = {
            policy_id: sum(left * right for left, right in zip(query_vector, document_vectors[policy_id], strict=True))
            for policy_id in case["candidate_policy_ids"]
        }
        ranking = sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))
        bm25 = _bm25_rank(case["query"], case["candidate_policy_ids"], dict(zip(policy_ids, sources, strict=True)))
        hybrid = _rrf_rank([bm25, ranking])
        rankings.append(ranking)
        hybrid_rankings.append(hybrid)
        bm25_rankings.append(bm25)
        rankings_by_case_id[case["case_id"]] = ranking
        hybrid_rankings_by_case_id[case["case_id"]] = hybrid
        bm25_rankings_by_case_id[case["case_id"]] = bm25
        top_results.append({
            "case_id": case["case_id"],
            "expected_policy_id": case["expected_policy_id"],
            "retrieved_policy_id": ranking[0],
            "cosine_similarity": scores[ranking[0]],
            "score_margin": scores[ranking[0]] - scores[ranking[1]],
            "bm25_top1": bm25[0],
            "qwen_bm25_agree": ranking[0] == bm25[0],
        })
        hybrid_top_results.append({
            "case_id": case["case_id"],
            "expected_policy_id": case["expected_policy_id"],
            "retrieved_policy_id": hybrid[0],
        })

    metrics = _metrics(cases, rankings)
    metrics["top1_results"] = top_results
    metrics["by_split"] = {
        split: _metrics(
            [case for case in cases if case["split"] == split],
            [rankings_by_case_id[case["case_id"]] for case in cases if case["split"] == split],
        )
        for split in sorted({case["split"] for case in cases})
    }
    hybrid_metrics = _metrics(cases, hybrid_rankings)
    hybrid_metrics["top1_results"] = hybrid_top_results
    hybrid_metrics["by_split"] = {
        split: _metrics(
            [case for case in cases if case["split"] == split],
            [hybrid_rankings_by_case_id[case["case_id"]] for case in cases if case["split"] == split],
        )
        for split in sorted({case["split"] for case in cases})
    }
    bm25_metrics = _metrics(cases, bm25_rankings)
    agreement_metrics = {}
    for split in sorted({case["split"] for case in cases}):
        split_cases = [case for case in cases if case["split"] == split]
        accepted_cases = [case for case in split_cases if rankings_by_case_id[case["case_id"]][0] == bm25_rankings_by_case_id[case["case_id"]][0]]
        accepted_rankings = [rankings_by_case_id[case["case_id"]] for case in accepted_cases]
        agreement_metrics[split] = {
            "cases": len(split_cases),
            "accepted": len(accepted_cases),
            "coverage": len(accepted_cases) / len(split_cases) if split_cases else 0.0,
            "selective_accuracy_at_1": _metrics(accepted_cases, accepted_rankings)["citation_accuracy_at_1"],
            "failed_case_ids": [case["case_id"] for case in accepted_cases if rankings_by_case_id[case["case_id"]][0] != case["expected_policy_id"]],
        }
    return {
        "model": Path(model_path).name,
        "device": "cpu",
        "similarity": "cosine (L2-normalized vectors)",
        "model_load_seconds": round(load_seconds, 3),
        "documents_embedding_seconds": round(document_seconds, 3),
        "queries_embedding_seconds": round(query_seconds, 3),
        "mean_query_embedding_ms": round(query_seconds * 1000 / len(cases), 2) if cases else None,
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("torch", "transformers", "sentence-transformers")
        },
        **metrics,
        "hybrid_bm25_qwen_rrf": hybrid_metrics,
        "bm25_baseline": bm25_metrics,
        "agreement_only_selection": agreement_metrics,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the deterministic offline policy retrieval benchmark.")
    parser.add_argument("--dataset", choices=("retrieval", "retrieval_v2"), default="retrieval")
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--with-local-embedding", action="store_true", help="Also evaluate the configured local Sentence Transformers model.")
    args = parser.parse_args()

    cases_path = DATA_DIR / ("cases_v2.jsonl" if args.dataset == "retrieval_v2" else "cases.jsonl")
    policy_path = DATA_DIR / "policies.json"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    policies = json.loads(policy_path.read_text(encoding="utf-8"))["policies"]
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    payload = {
        "dataset_sha256": hashlib.sha256(cases_path.read_bytes() + policy_path.read_bytes()).hexdigest(),
        "dataset": args.dataset,
        "split": args.split,
        **evaluate_offline(cases, policies),
    }
    if args.with_local_embedding:
        if not settings.local_embedding_model_path:
            parser.error("Set LOCAL_EMBEDDING_MODEL_PATH or configure it in .env before local model evaluation.")
        payload["local_model_evaluation"] = evaluate_local_model(cases, policies, settings.local_embedding_model_path)
    print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
