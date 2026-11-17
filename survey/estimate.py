import math


def weighted_mean(rows: list[dict], field: str) -> dict[str, float | int | None]:
    answered = [row for row in rows if row.get("responded") and row.get(field) is not None]
    total_weight = sum(float(row["weight"]) for row in answered)
    if not answered or total_weight <= 0:
        return {"estimate": None, "standard_error": None, "effective_n": 0}
    estimate = sum(float(row[field]) * float(row["weight"]) for row in answered) / total_weight
    squared = sum(float(row["weight"]) ** 2 for row in answered)
    effective_n = total_weight**2 / squared if squared else 0
    variance = sum(float(row["weight"]) * (float(row[field]) - estimate) ** 2 for row in answered) / total_weight
    return {"estimate": estimate, "standard_error": math.sqrt(variance / effective_n) if effective_n else None, "effective_n": round(effective_n)}
