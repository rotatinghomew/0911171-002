from .estimate import weighted_mean


def summarize(rows: list[dict], field: str) -> dict:
    strata = sorted({row["stratum"] for row in rows})
    return {
        "overall": weighted_mean(rows, field),
        "strata": {
            stratum: weighted_mean([row for row in rows if row["stratum"] == stratum], field)
            for stratum in strata
        },
        "response_count": sum(bool(row.get("responded")) for row in rows),
        "unit_count": len(rows),
    }
