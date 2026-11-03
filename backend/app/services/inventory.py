from collections import defaultdict
from collections.abc import Iterable


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
