def calibrate_weights(rows: list[dict], target_by_stratum: dict[str, float]) -> list[dict]:
    totals: dict[str, float] = {}
    for row in rows:
        if row.get("responded"):
            stratum = row["stratum"]
            totals[stratum] = totals.get(stratum, 0.0) + float(row["base_weight"])
    result = []
    for row in rows:
        updated = dict(row)
        stratum = row["stratum"]
        target = target_by_stratum.get(stratum, 0.0)
        scale = target / totals[stratum] if row.get("responded") and totals.get(stratum, 0) else 0.0
        updated["weight"] = float(row["base_weight"]) * scale
        result.append(updated)
    return result
