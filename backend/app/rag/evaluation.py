import math
import re
from collections import Counter
from typing import Any


TOPIC_TERMS = {
    "EVAL-BULK": ("bulk", "volume", "wholesale", "quantity", "lot", "large lot", "case lot", "pallet"),
    "EVAL-LOYALTY": ("loyalty", "loyal", "repeat", "returning", "long standing", "relationship", "renewal", "regular", "member"),
    "EVAL-EARLYPAY": ("prepay", "prepayment", "early payment", "settle", "invoice", "advance", "upfront", "cash", "promptly"),
    "EVAL-SEASON": ("seasonal", "holiday", "spring", "summer", "year end", "festive", "campaign", "event"),
    "EVAL-PRODUCT": ("product a", "product specific", "catalog", "category", "series", "selected product", "product line", "model"),
}
TOKEN_PATTERN = re.compile(r"[a-z]+")


def _tokens(text: str) -> list[str]:
    return TOKEN_PATTERN.findall(text.lower())


def _bm25_rank(query: str, candidate_ids: list[str], sources: dict[str, str]) -> list[str]:
    documents = {policy_id: _tokens(sources[policy_id]) for policy_id in candidate_ids}
    query_terms = set(_tokens(query))
    average_length = sum(map(len, documents.values())) / max(len(documents), 1)
    scores: dict[str, float] = {}
    for policy_id, document in documents.items():
        frequency_by_term = Counter(document)
        score = 0.0
        for term in query_terms:
            frequency = frequency_by_term[term]
            if not frequency:
                continue
            document_frequency = sum(term in other for other in documents.values())
            inverse_frequency = math.log(1 + (len(documents) - document_frequency + 0.5) / (document_frequency + 0.5))
            score += inverse_frequency * frequency * 2.5 / (
                frequency + 1.5 * (1 - 0.75 + 0.75 * len(document) / average_length)
            )
        scores[policy_id] = score
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _dense_test_rank(query: str, candidate_ids: list[str], sources: dict[str, str]) -> list[str]:
    normalized_query = re.sub(r"[-\s]+", " ", query.lower())
    query_vector = [float(any(term in normalized_query for term in terms)) for terms in TOPIC_TERMS.values()]
    scores: dict[str, float] = {}
    for policy_id in candidate_ids:
        normalized_source = re.sub(r"[-\s]+", " ", sources[policy_id].lower())
        source_vector = [float(any(term in normalized_source for term in terms)) for terms in TOPIC_TERMS.values()]
        scores[policy_id] = sum(left * right for left, right in zip(query_vector, source_vector))
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _rrf_rank(rankings: list[list[str]], k: int = 60) -> list[str]:
    scores: dict[str, float] = {}
    for ranking in rankings:
        for rank, policy_id in enumerate(ranking, start=1):
            scores[policy_id] = scores.get(policy_id, 0.0) + 1 / (k + rank)
    return sorted(scores, key=lambda policy_id: (-scores[policy_id], policy_id))


def _metrics(cases: list[dict[str, Any]], rankings: list[list[str]]) -> dict[str, float | int]:
    reciprocal_ranks = []
    hits_at_1 = 0
    hits_at_3 = 0
    for case, ranking in zip(cases, rankings):
        expected = case["expected_policy_id"]
        position = next((index for index, candidate in enumerate(ranking, start=1) if candidate == expected), None)
        reciprocal_ranks.append(1 / position if position else 0.0)
        hits_at_1 += int(position == 1)
        hits_at_3 += int(position is not None and position <= 3)
    total = len(cases)
    return {
        "cases": total,
        "citation_accuracy_at_1": hits_at_1 / total if total else 0.0,
        "recall_at_3": hits_at_3 / total if total else 0.0,
        "mrr": sum(reciprocal_ranks) / total if total else 0.0,
    }


def evaluate_offline(cases: list[dict[str, Any]], policies: list[dict[str, Any]]) -> dict[str, Any]:
    sources = {policy["policy_id"]: policy["source"] for policy in policies}
    methods: dict[str, list[list[str]]] = {"bm25": [], "deterministic_dense_test_adapter": [], "rrf": []}
    for case in cases:
        candidate_ids = case["candidate_policy_ids"]
        bm25 = _bm25_rank(case["query"], candidate_ids, sources)
        dense = _dense_test_rank(case["query"], candidate_ids, sources)
        methods["bm25"].append(bm25)
        methods["deterministic_dense_test_adapter"].append(dense)
        methods["rrf"].append(_rrf_rank([bm25, dense]))
    return {
        "offline_test_adapter_only": True,
        "real_model_evaluation": False,
        "methods": {name: _metrics(cases, rankings) for name, rankings in methods.items()},
    }
