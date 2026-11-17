"""权重构造与校准。

包含三套口径，发布记录中必须注明实际使用的是哪一套：

- :func:`calibrate_weights`：旧版单层（仅分层总量）事后分层校准，保留以
  维持既有发布可复核；
- :func:`phase_base_weights`：按回收批次构造的入样基础权重，补访单位
  带二阶段概率倒数，不与初始等概率回收混用；
- :func:`rake_weights`：多边际迭代加权（IPF/raking），同时对齐地区、
  年龄等多个人口边际；无样本支持的单元格直接报错，不做任何静默修补。
"""
import math

from .batches import INITIAL_BATCH, phase_base_weight
from .benchmark import Benchmark, BenchmarkError


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


def phase_base_weights(rows: list[dict]) -> list[dict]:
    """按批次写入 ``phase_weight``；未应答单位权重为零但记录保留。"""
    result = []
    for row in rows:
        updated = dict(row)
        if row.get("responded"):
            updated["phase_weight"] = phase_base_weight(row)
        else:
            updated["phase_weight"] = 0.0
        result.append(updated)
    return result


def rake_weights(
    rows: list[dict],
    benchmark: Benchmark,
    dimension_fields: dict[str, str],
    max_iterations: int = 500,
    tolerance: float = 1e-6,
    extreme_ratio: float = 10.0,
) -> dict:
    """多边际迭代加权校准。

    流程：

    1. 基准支持性校验（缺失取值、无支持单元格直接抛错）；
    2. 以批次入样基础权重（``phase_weight``，缺失时退回 ``base_weight``）
       为起点；
    3. 逐维度做比例调整，直到每个维度边际相对偏差低于 ``tolerance`` 或
       达到 ``max_iterations``；不收敛时报错，不返回半成品权重；
    4. 返回校准后行、迭代次数、最大调整因子与极端权重诊断。

    未应答单位不参与校准，权重为零；其记录仍留在结果中，区别于填零答案。
    """
    benchmark.check_support(rows, dimension_fields)
    if max_iterations <= 0:
        raise BenchmarkError("迭代上限必须为正整数")

    respondents = [dict(row) for row in rows if row.get("responded")]
    for row in respondents:
        row["weight"] = float(row.get("phase_weight") or row["base_weight"])

    target_total = benchmark.population_total
    history: list[dict] = []
    max_adjustment_factor = 1.0
    window = 30
    for iteration in range(1, max_iterations + 1):
        worst_gap = 0.0
        for dimension, categories in benchmark.margins.items():
            field = dimension_fields[dimension]
            current: dict[str, float] = {category: 0.0 for category in categories}
            for row in respondents:
                current[str(row[field])] += row["weight"]
            for category, target in categories.items():
                observed = current[category]
                if observed <= 0:
                    # 理论上由 check_support 拦截，这里防御性兜底，绝不抹平。
                    raise BenchmarkError(
                        f"维度 {dimension} 类别 {category} 校准中途失去样本支持"
                    )
                factor = target / observed
                max_adjustment_factor = max(max_adjustment_factor, factor, 1.0 / factor)
                worst_gap = max(worst_gap, abs(observed - target) / target)
                for row in respondents:
                    if str(row[field]) == category:
                        row["weight"] *= factor
        history.append({"iteration": iteration, "max_relative_gap": worst_gap})
        if worst_gap <= tolerance:
            break
        # 结构零单元格会使 IPF 渐近停滞在非零偏差：偏差仍在下降但改善率
        # 趋于零，永远到不了容差。检测到改善率枯竭即报错并指出空联合
        # 单元格，禁止跑满迭代后悄悄返回不满足边际的权重。
        if iteration > window:
            oldest = history[-window - 1]["max_relative_gap"]
            recent_floor = min(item["max_relative_gap"] for item in history[-window:])
            relative_improvement = (oldest - recent_floor) / oldest
            if relative_improvement < 1e-3 and worst_gap > tolerance * 100:
                empty_cells = _empty_joint_cells(respondents, benchmark, dimension_fields)
                raise BenchmarkError(
                    f"raking 在第 {iteration} 轮停滞（相对偏差 {worst_gap:.3g}，"
                    f"近 {window} 轮改善不足）：回收样本的联合分布存在结构零单元格，"
                    "无法同时精确满足全部边际，"
                    f"空缺联合单元格：{empty_cells}。请补充相应样本或修订基准，"
                    "禁止以算法迭代抹平该矛盾"
                )
    else:
        raise BenchmarkError(
            f"raking 在 {max_iterations} 次迭代后仍未收敛（末轮相对偏差 "
            f"{history[-1]['max_relative_gap']:.3g}），请检查基准或样本结构"
        )

    # 校准后总权重应等于人口总量；显式校验，偏差不可被悄悄吸收。
    calibrated_total = sum(row["weight"] for row in respondents)
    if abs(calibrated_total - target_total) > tolerance * target_total * 10:
        raise BenchmarkError(
            f"校准后总权重 {calibrated_total:g} 与基准人口 {target_total:g} 不一致"
        )

    by_id = {row["unit_id"]: row for row in respondents}
    output = []
    for row in rows:
        updated = dict(row)
        if row.get("responded"):
            updated["weight"] = by_id[row["unit_id"]]["weight"]
        else:
            updated["weight"] = 0.0
        output.append(updated)

    diagnostics = weight_diagnostics(output, extreme_ratio=extreme_ratio)
    diagnostics.update(
        iterations=history[-1]["iteration"],
        max_relative_gap=history[-1]["max_relative_gap"],
        max_adjustment_factor=max_adjustment_factor,
        convergence_history=history,
    )
    return {"rows": output, "diagnostics": diagnostics}


def _empty_joint_cells(rows: list[dict], benchmark: Benchmark, dimension_fields: dict[str, str]) -> list[dict]:
    """列出基准类别交叉后在样本中完全空缺的联合单元格。"""
    dimensions = list(benchmark.margins)
    if len(dimensions) < 2:
        return []
    observed = {
        tuple(str(row[dimension_fields[dimension]]) for dimension in dimensions)
        for row in rows
    }
    empty: list[dict] = []
    first, *rest = dimensions
    for head in benchmark.margins[first]:
        for combination in _category_product(benchmark, rest):
            candidate = (head, *combination)
            if candidate not in observed:
                empty.append({
                    dimension: category
                    for dimension, category in zip(dimensions, candidate)
                })
    return empty


def _category_product(benchmark: Benchmark, dimensions: list[str]) -> list[tuple]:
    if not dimensions:
        return [()]
    head, *rest = dimensions
    tails = _category_product(benchmark, rest)
    return [
        (category, *tail)
        for category in benchmark.margins[head]
        for tail in tails
    ]


def weight_diagnostics(rows: list[dict], extreme_ratio: float = 10.0) -> dict:
    """权重集中度诊断。

    - 最大/最小权重、权重比与变异系数；
    - 有效样本量 Kish n_eff = (Σw)²/Σw²；
    - 最大权重单位占总权重的份额；
    - 超过最小权重 ``extreme_ratio`` 倍的极端权重清单（只标记，不修剪、
      不缩尾——任何处理都必须由业务显式决定并记录在发布中）。
    """
    respondents = [row for row in rows if row.get("responded") and float(row.get("weight", 0.0)) > 0]
    if not respondents:
        return {
            "respondent_count": 0,
            "min_weight": None,
            "max_weight": None,
            "weight_ratio": None,
            "coefficient_of_variation": None,
            "effective_sample_size": 0,
            "largest_weight_share": None,
            "extreme_weights": [],
        }
    weights = [float(row["weight"]) for row in respondents]
    total = sum(weights)
    squared = sum(value * value for value in weights)
    mean = total / len(weights)
    variance = sum((value - mean) ** 2 for value in weights) / len(weights)
    minimum, maximum = min(weights), max(weights)
    effective = total * total / squared
    extreme_threshold = minimum * extreme_ratio
    extreme = [
        {
            "unit_id": row["unit_id"],
            "stratum": row["stratum"],
            "cluster": row.get("cluster"),
            "response_batch": row.get("response_batch", INITIAL_BATCH),
            "weight": float(row["weight"]),
            "ratio_to_min": float(row["weight"]) / minimum,
        }
        for row in respondents
        if float(row["weight"]) >= extreme_threshold and float(row["weight"]) > minimum
    ]
    extreme.sort(key=lambda item: item["weight"], reverse=True)
    largest_unit = max(respondents, key=lambda row: float(row["weight"]))
    return {
        "respondent_count": len(respondents),
        "min_weight": minimum,
        "max_weight": maximum,
        "weight_ratio": maximum / minimum,
        "coefficient_of_variation": math.sqrt(variance) / mean if mean else None,
        "effective_sample_size": effective,
        "largest_weight_share": max(weights) / total,
        "largest_weight_unit": largest_unit["unit_id"],
        "extreme_ratio_threshold": extreme_ratio,
        "extreme_weights": extreme,
    }
