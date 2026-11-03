"""Evaluate the configured chat model against frozen inquiry extraction sets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import importlib.metadata
import json
from pathlib import Path
import statistics
import sys
from time import perf_counter
from urllib.parse import urlsplit


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))

from app.agents.workflow import _parse_intent  # noqa: E402
from app.core.config import settings  # noqa: E402


DATA_DIR = ROOT / "data" / "policy_retrieval_eval"
EDGE_CASES_PATH = ROOT / "data" / "intent_parse_eval" / "cases.jsonl"
EDGE_V2_CASES_PATH = ROOT / "data" / "intent_parse_eval" / "cases_v2.jsonl"


def _normalized_items(intent: dict) -> list[dict]:
    items = intent.get("items") or []
    if items:
        return [
            {
                "product_id": item.get("product_id"),
                "quantity": item.get("quantity"),
                "requested_discount_pct": _decimal_string(item.get("requested_discount_pct")),
            }
            for item in items
        ]
    legacy_item = {
            "product_id": intent.get("product_id"),
            "quantity": intent.get("quantity"),
            "requested_discount_pct": _decimal_string(intent.get("requested_discount_pct")),
        }
    return [] if all(value is None for value in legacy_item.values()) else [legacy_item]


def _decimal_string(value) -> str | None:
    if value is None:
        return None
    try:
        return format(Decimal(str(value)).normalize(), "f")
    except InvalidOperation:
        return str(value)


def _expected_items(case: dict) -> list[dict]:
    if "expected_items" in case:
        items = case["expected_items"]
    else:
        items = [{
            "product_id": "P-100",
            "quantity": case["quantity"],
            "requested_discount_pct": case["requested_discount_pct"],
        }]
    return [
        {
            "product_id": item.get("product_id"),
            "quantity": item.get("quantity"),
            "requested_discount_pct": _decimal_string(item.get("requested_discount_pct")),
        }
        for item in items
    ]


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return round(ordered[index], 1)


def _run(cases: list[dict], run_number: int) -> dict:
    rows = []
    latencies = []
    input_tokens = 0
    output_tokens = 0

    for case in cases:
        inquiry = case.get("inquiry") or f"P-100. {case['query']}"
        started = perf_counter()
        try:
            parsed = _parse_intent({"raw_inquiry": inquiry, "events": []})
            elapsed_ms = round((perf_counter() - started) * 1000, 1)
            intent = parsed["intent"]
            actual_items = _normalized_items(intent)
            expected_items = _expected_items(case)
            actual = {
                "items": actual_items,
                "customer_message_matches": intent.get("customer_message") == inquiry,
                "item_count": len(actual_items),
            }
            expected = {
                "items": expected_items,
                "customer_message_matches": True,
                "item_count": len(expected_items),
            }
            field_matches = {
                "product_ids": [item["product_id"] for item in actual_items] == [item["product_id"] for item in expected_items],
                "quantities": [item["quantity"] for item in actual_items] == [item["quantity"] for item in expected_items],
                "discounts": [item["requested_discount_pct"] for item in actual_items] == [item["requested_discount_pct"] for item in expected_items],
                "customer_message_matches": actual["customer_message_matches"],
                "item_count": actual["item_count"] == expected["item_count"],
            }
            usage = parsed.get("usage") or {}
            row = {
                "case_id": case["case_id"],
                "split": case["split"],
                "language": case.get("language", "en"),
                "status": "success",
                "latency_ms": elapsed_ms,
                "field_matches": field_matches,
                "expected": expected,
                "actual": actual,
                "input_tokens": usage.get("input_tokens"),
                "output_tokens": usage.get("output_tokens"),
            }
            latencies.append(elapsed_ms)
            if usage.get("input_tokens") is not None:
                input_tokens += usage["input_tokens"]
            if usage.get("output_tokens") is not None:
                output_tokens += usage["output_tokens"]
        except Exception as exc:  # Record failures without request headers or credentials.
            row = {
                "case_id": case["case_id"],
                "split": case["split"],
                "language": case.get("language", "en"),
                "status": "error",
                "latency_ms": round((perf_counter() - started) * 1000, 1),
                "error_type": type(exc).__name__,
                "status_code": getattr(exc, "status_code", None),
            }
        rows.append(row)

    by_split = {}
    for split in sorted({case["split"] for case in cases}):
        split_rows = [row for row in rows if row["split"] == split]
        successful = [row for row in split_rows if row["status"] == "success"]
        exact = [row for row in successful if all(row["field_matches"].values())]
        by_split[split] = {
            "cases": len(split_rows),
            "successful_calls": len(successful),
            "exact_matches": len(exact),
            "exact_match_rate": len(exact) / len(split_rows) if split_rows else 0.0,
        }

    successful = [row for row in rows if row["status"] == "success"]
    exact = [row for row in successful if all(row["field_matches"].values())]
    field_accuracy = {
        field: sum(row["field_matches"][field] for row in successful) / len(successful) if successful else 0.0
        for field in ("product_ids", "quantities", "discounts", "customer_message_matches", "item_count")
    }
    by_language = {}
    for language in sorted({case.get("language", "en") for case in cases}):
        language_rows = [row for row in successful if row["language"] == language]
        exact_language = [row for row in language_rows if all(row["field_matches"].values())]
        by_language[language] = {"cases": len(language_rows), "exact_matches": len(exact_language), "exact_match_rate": len(exact_language) / len(language_rows) if language_rows else 0.0}
    return {
        "run": run_number,
        "cases": len(rows),
        "successful_calls": len(successful),
        "failed_calls": len(rows) - len(successful),
        "exact_matches": len(exact),
        "exact_match_rate": len(exact) / len(rows) if rows else 0.0,
        "field_accuracy": field_accuracy,
        "latency_ms": {
            "mean": round(statistics.mean(latencies), 1) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
        },
        "tokens": {"input": input_tokens, "output": output_tokens, "total": input_tokens + output_tokens},
        "by_split": by_split,
        "by_language": by_language,
        "failures": [row for row in rows if row["status"] == "error" or not all(row.get("field_matches", {}).values())],
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate the configured chat model on frozen inquiry extraction sets.")
    parser.add_argument("--dataset", choices=("retrieval", "edge", "edge_v2"), default="retrieval")
    parser.add_argument("--runs", type=int, choices=(1, 2, 3), default=2)
    parser.add_argument("--split", choices=("all", "development", "holdout"), default="all")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    if not settings.llm_api_key or not settings.llm_model:
        parser.error("Configure LLM_API_KEY and LLM_MODEL in .env before running the live evaluation.")

    cases_path = {
        "edge": EDGE_CASES_PATH,
        "edge_v2": EDGE_V2_CASES_PATH,
        "retrieval": DATA_DIR / "cases.jsonl",
    }[args.dataset]
    policy_path = DATA_DIR / "policies.json"
    cases = [json.loads(line) for line in cases_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if args.split != "all":
        cases = [case for case in cases if case["split"] == args.split]
    dataset_bytes = cases_path.read_bytes() + (policy_path.read_bytes() if args.dataset == "retrieval" else b"")
    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "model": settings.llm_model,
        "base_url_host": urlsplit(settings.llm_base_url).hostname if settings.llm_base_url else "provider_default",
        "split": args.split,
        "dataset": args.dataset,
        "dataset_sha256": hashlib.sha256(dataset_bytes).hexdigest(),
        "cases": len(cases),
        "runs": [_run(cases, run_number) for run_number in range(1, args.runs + 1)],
        "packages": {
            name: importlib.metadata.version(name)
            for name in ("langchain-openai", "openai")
        },
    }
    rendered = json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)


if __name__ == "__main__":
    main()
