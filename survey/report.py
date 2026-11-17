"""发布记录组装。

一条发布记录是自洽、可序列化、可回放的快照：采用的抽样设计、回收批次
与选取机制、人口基准、校准设定与收敛情况、校准前后的总体/地区估计、
权重集中度、区间依据以及全部限制说明都在其中。旧发布不被后续回收覆盖，
换基准或补回答案后以新发布编号重做，比较时逐条核对指纹即可。
"""
from copy import deepcopy

from .batches import INITIAL_BATCH
from .estimate import estimate_field, weighted_mean
from .weights import phase_base_weights, rake_weights, weight_diagnostics


def _design_basis(design: dict | None) -> dict:
    if design is None:
        return {"design_kind": "external_sample", "note": "样本未经 SampleDesign 登记，设计信息缺失"}
    keys = (
        "design_id",
        "design_kind",
        "cluster_field",
        "quotas",
        "cluster_quotas",
        "unit_quotas",
        "seed",
        "frame_snapshot",
    )
    return {key: deepcopy(design[key]) for key in keys if key in design}


def _batch_summary(rows: list[dict], followups: dict[str, dict]) -> dict:
    counts: dict[str, int] = {}
    for row in rows:
        if row.get("responded"):
            counts[row.get("response_batch", INITIAL_BATCH)] = (
                counts.get(row.get("response_batch", INITIAL_BATCH), 0) + 1
            )
    return {
        "respondents_by_batch": dict(sorted(counts.items())),
        "unit_nonresponse_count": sum(1 for row in rows if not row.get("responded")),
        "followup_plans": {
            batch_id: {
                "seed": plan["seed"],
                "mechanism": deepcopy(plan["mechanism"]),
                "eligible_count": plan["eligible_count"],
                "roster_size": len(plan["roster"]),
                "roster": deepcopy(plan["roster"]),
            }
            for batch_id, plan in sorted(followups.items())
        },
    }


def _domain_rows(rows: list[dict], domain_field: str | None) -> dict[str | None, list[dict]]:
    if domain_field is None:
        return {None: rows}
    domains: dict[str | None, list[dict]] = {None: rows}
    for row in rows:
        value = row.get(domain_field)
        key = "缺失" if value is None else str(value)
        domains.setdefault(key, []).append(row)
    return domains


def _field_estimates(
    rows: list[dict],
    fields: list[str],
    cluster_field: str,
    confidence: float,
    frame_snapshot: dict | None,
    domain_field: str | None,
) -> dict:
    output: dict[str, dict] = {}
    for field in fields:
        field_block: dict = {"overall": {}, "domains": {}}
        for domain, subset in _domain_rows(rows, domain_field).items():
            estimate = estimate_field(
                subset,
                field,
                cluster_field=cluster_field,
                confidence=confidence,
                frame_snapshot=frame_snapshot,
                kind="mean",
                domain_label=domain,
            )
            total = estimate_field(
                subset,
                field,
                cluster_field=cluster_field,
                confidence=confidence,
                frame_snapshot=frame_snapshot,
                kind="total",
                domain_label=domain,
            )
            block = {"mean": estimate, "total": total}
            if domain is None:
                field_block["overall"] = block
            else:
                field_block["domains"][domain] = block
        output[field] = field_block
    return output


def build_release(
    *,
    release_id: str,
    design: dict | None,
    rows: list[dict],
    benchmark,
    fields: list[str],
    dimension_fields: dict[str, str],
    followups: dict[str, dict],
    confidence: float,
    extreme_ratio: float,
    max_iterations: int,
    domain_field: str | None,
    note: str | None,
) -> dict:
    """构造校准发布记录；任何无法支持的单元格都以异常上抛，不做静默处理。"""
    design_basis = _design_basis(design)
    cluster_field = (design or {}).get("cluster_field", "cluster")
    frame_snapshot = (design or {}).get("frame_snapshot")

    phased = phase_base_weights(rows)
    # 校准前：仅入样概率（含补访二阶段）权重，不做任何人口对齐。
    before_rows = [
        {**row, "weight": row["phase_weight"] if row.get("responded") else 0.0}
        for row in phased
    ]
    calibration = rake_weights(
        phased,
        benchmark,
        dimension_fields,
        max_iterations=max_iterations,
        extreme_ratio=extreme_ratio,
    )
    after_rows = calibration["rows"]

    settings = {
        "method": "iterative_proportional_fitting_raking",
        "dimension_fields": dict(dimension_fields),
        "max_iterations": max_iterations,
        "extreme_ratio_threshold": extreme_ratio,
        "confidence_level": confidence,
        "variance": "ultimate_cluster_linearization",
        "cluster_field": cluster_field,
        "finite_population_correction": frame_snapshot is not None,
        "item_nonresponse": "fieldwise_exclusion_no_imputation",
        "unit_nonresponse": "retained_in_frame_weight_zero",
    }

    return {
        "release_id": release_id,
        "release_kind": "calibrated_population_estimate",
        "note": note,
        "design": design_basis,
        "batches": _batch_summary(rows, followups),
        "benchmark": benchmark.to_record(),
        "calibration_settings": settings,
        "calibration_diagnostics": calibration["diagnostics"],
        "estimates": {
            "before_calibration": _field_estimates(
                before_rows, fields, cluster_field, confidence, frame_snapshot, domain_field
            ),
            "after_calibration": _field_estimates(
                after_rows, fields, cluster_field, confidence, frame_snapshot, domain_field
            ),
        },
        "weighted_rows": _slim_rows(after_rows),
        "limitations": _collect_limitations(rows, after_rows, calibration["diagnostics"]),
    }


def sample_description(
    *,
    release_id: str,
    design: dict | None,
    rows: list[dict],
    fields: list[str],
    followups: dict[str, dict],
    reason: str,
    confidence: float,
    domain_field: str | None,
    note: str | None,
) -> dict:
    """不做总体外推的样本描述发布。"""
    design_basis = _design_basis(design)
    cluster_field = (design or {}).get("cluster_field", "cluster")
    frame_snapshot = (design or {}).get("frame_snapshot")
    phased = phase_base_weights(rows)
    described = [
        {**row, "weight": row["phase_weight"] if row.get("responded") else 0.0}
        for row in phased
    ]
    return {
        "release_id": release_id,
        "release_kind": "sample_description_only",
        "note": note,
        "design": design_basis,
        "batches": _batch_summary(rows, followups),
        "benchmark": None,
        "calibration_settings": None,
        "calibration_diagnostics": weight_diagnostics(described),
        "estimates": {
            "sample_description": _field_estimates(
                described, fields, cluster_field, confidence, frame_snapshot, domain_field
            )
        },
        "weighted_rows": _slim_rows(described),
        "limitations": [
            f"不做总体外推：{reason}",
            "本记录仅描述观测样本，权重为入样概率倒数，未对齐任何人口边际",
            *_collect_limitations(rows, described, weight_diagnostics(described)),
        ],
    }


def legacy_release(release_id: str, design: dict | None, rows: list[dict], margins: dict[str, float], field: str) -> dict:
    """旧版单层边际发布的记录化封装，保持历史可核对。"""
    return {
        "release_id": release_id,
        "release_kind": "legacy_stratum_poststratification",
        "design": _design_basis(design),
        "margins": deepcopy(margins),
        "field": field,
        "summary": summarize(rows, field),
        "limitations": [
            "旧口径：仅按分层总量校准，未对齐年龄等其他边际",
            "旧口径：方差按单位独立计算，未尊重服务点整群结构",
        ],
    }


def _slim_rows(rows: list[dict]) -> list[dict]:
    keep = {
        "unit_id",
        "stratum",
        "cluster",
        "response_batch",
        "phase_probability",
        "base_weight",
        "inclusion_probability",
        "weight",
        "responded",
    }
    return [{key: deepcopy(row[key]) for key in keep if key in row} for row in rows]


def _collect_limitations(rows: list[dict], weighted_rows: list[dict], diagnostics: dict) -> list[str]:
    limitations: list[str] = []
    item_missing = sorted(
        {
            key
            for row in rows
            if row.get("responded")
            for key, value in row.items()
            if key not in {"responded", "unit_id"} and value is None
        }
    )
    if item_missing:
        limitations.append(f"单项未答字段（按字段剔除，未填零）：{item_missing}")
    unit_missing = sum(1 for row in rows if not row.get("responded"))
    if unit_missing:
        limitations.append(f"整份未回收单位 {unit_missing} 个，保留在抽样框中且权重为零")
    if diagnostics.get("extreme_weights"):
        limitations.append(
            f"存在 {len(diagnostics['extreme_weights'])} 个极端权重（≥最小权重 "
            f"{diagnostics.get('extreme_ratio_threshold')} 倍），仅标记未修剪，"
            "结果对这些单位敏感"
        )
    return limitations


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
