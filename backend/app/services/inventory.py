from collections import defaultdict
from collections.abc import Iterable


def order_constraint_failure(
    lines: Iterable[tuple[str, str, int, int]],
) -> tuple[str, str | None, int | None, int | None] | None:
    """Validate quote-wide currency and aggregate per-SKU stock constraints."""
    materialized = list(lines)
    if len({currency for _, currency, _, _ in materialized}) > 1:
        return "MIXED_CURRENCY_UNSUPPORTED", None, None, None
    inventory_failure = aggregate_inventory_failure(
        (product_id, quantity, stock) for product_id, _, quantity, stock in materialized
    )
    if inventory_failure:
        product_id, requested, available = inventory_failure
        return "INSUFFICIENT_STOCK", product_id, requested, available
    return None


def aggregate_inventory_failure(lines: Iterable[tuple[str, int, int]]) -> tuple[str, int, int] | None:
    quantities: dict[str, int] = defaultdict(int)
    stocks: dict[str, int] = {}
    for product_id, quantity, stock in lines:
        quantities[product_id] += quantity
        stocks[product_id] = stock
    for product_id, quantity in quantities.items():
        if quantity > stocks[product_id]:
            return product_id, quantity, stocks[product_id]
    return None
