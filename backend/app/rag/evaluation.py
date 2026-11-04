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
ENGLISH_STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "been", "being", "but", "by",
    "can", "could", "do", "does", "for", "from", "had", "has", "have", "how",
    "i", "if", "in", "into", "is", "it", "its", "me", "my", "of", "on", "or",
    "our", "please", "should", "the", "their", "them", "there", "these", "this",
    "those", "to", "was", "we", "were", "what", "when", "where", "which", "who",
    "will", "with", "would", "you", "your",
}
BILINGUAL_POLICY_TERMS = {
    "批量": "bulk volume quantity",
    "大批量": "bulk high-volume quantity",
    "大宗采购": "wholesale bulk purchase",
    "采购": "purchase order",
    "价格减让": "discount concession",
    "折扣": "discount rebate price reduction",
    "提前付款": "prepay upfront early payment",
    "预付款": "prepay upfront payment",
    "及时结款": "prompt payment settle",
    "七天内": "within seven days",
    "付清": "settle payment",
    "发票": "invoice bill",
    "指定系列": "designated series product line",
    "产品类别": "product category",
    "产品线": "product line",
    "商品系列": "product series catalog item",
    "专属商品": "product-specific catalog item",
    "会员回馈": "loyalty rebate member benefit",
    "忠诚客户": "loyal customer loyalty",
    "长期合作": "long-standing relationship",
    "续购": "repeat purchase",
    "回头客": "returning customer",
    "年末活动": "year-end campaign",
    "节日促销": "holiday promotion campaign",
    "活动折扣": "campaign discount promotion",
}


def _tokens(text: str, include_cjk: bool = False) -> list[str]:
    lowered = text.lower()
    tokens = [token for token in TOKEN_PATTERN.findall(lowered) if token not in ENGLISH_STOPWORDS]
    if include_cjk:
        for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
            tokens.extend([run] if len(run) == 1 else [run[i:i + 2] for i in range(len(run) - 1)])
    return tokens


def _bilingual_query_matches(query: str) -> list[str]:
    lowered = query.lower()
    return [phrase for phrase in BILINGUAL_POLICY_TERMS if phrase in lowered]


def _expand_bilingual_query(query: str) -> str:
    expansions = [BILINGUAL_POLICY_TERMS[phrase] for phrase in _bilingual_query_matches(query)]
    return f"{query} {' '.join(expansions)}" if expansions else query


def _bm25_scores(query: str, candidate_ids: list[str], sources: dict[str, str], include_cjk: bool = False) -> dict[str, float]:
    documents = {policy_id: _tokens(sources[policy_id], include_cjk) for policy_id in candidate_ids}
    expanded_query = _expand_bilingual_query(query) if include_cjk else query
    query_terms = sorted(set(_tokens(expanded_query, include_cjk)))
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
    return scores


def _bm25_rank(query: str, candidate_ids: list[str], sources: dict[str, str], include_cjk: bool = False) -> list[str]:
    scores = _bm25_scores(query, candidate_ids, sources, include_cjk)
    return sorted(scores, key=lambda candidate_id: (-scores[candidate_id], candidate_id))


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


def _metrics(cases: list[dict[str, Any]], rankings: list[list[str]]) -> dict[str, Any]:
    reciprocal_ranks = []
    failed_case_ids = []
    hits_at_1 = 0
    hits_at_3 = 0
    for case, ranking in zip(cases, rankings):
        expected = case["expected_policy_id"]
        position = next((index for index, candidate in enumerate(ranking, start=1) if candidate == expected), None)
        reciprocal_ranks.append(1 / position if position else 0.0)
        hits_at_1 += int(position == 1)
        hits_at_3 += int(position is not None and position <= 3)
        if position != 1:
            failed_case_ids.append(case["case_id"])
    total = len(cases)
    return {
        "cases": total,
        "citation_accuracy_at_1": hits_at_1 / total if total else 0.0,
        "recall_at_3": hits_at_3 / total if total else 0.0,
        "mrr": sum(reciprocal_ranks) / total if total else 0.0,
        "failed_case_ids": failed_case_ids,
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
