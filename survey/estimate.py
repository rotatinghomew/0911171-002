"""调查点估计与抽样不确定性。

- :func:`weighted_mean`：旧版朴素估计，把每个应答者当作独立单位，仅保留
  用于历史发布的可复核；整群设计禁止使用该口径。
- :func:`estimate_field`：按实际抽样单位（服务点 PSU）计算的 ultimate
  cluster 线性化方差。同一服务点内的单位共享一个随机残差项，不会被当作
  独立抽样；自由度取 ``Σ_h(抽中PSU数 - 1)``，区间用 t 分位数而非正态
  分位数，并可在设计记录提供框规模时施加 FPC。

单项未答（字段为 ``None``）只从该字段的估计中剔除，权重与其余字段不受
影响，绝不按零处理；整份未回收的单位根本不进入应答集。没有样本支持的
域返回 ``estimate=None`` 并写明原因，而不是输出零或外推值。
"""
import math

from scipy.stats import t as t_distribution


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


def _cluster_identity(row: dict, cluster_field: str) -> tuple[str, str]:
    cluster = row.get(cluster_field)
    if cluster is None:
        # 非整群设计：每个单位自身视为一个 PSU，退化到独立单位口径。
        cluster = f"unit::{row['unit_id']}"
    return row["stratum"], str(cluster)


def estimate_field(
    rows: list[dict],
    field: str,
    *,
    cluster_field: str = "cluster",
    confidence: float = 0.95,
    frame_snapshot: dict | None = None,
    kind: str = "mean",
    domain_label: str | None = None,
) -> dict:
    """带聚类稳健方差的加权估计。

    参数
    ----
    rows:
        已带 ``weight`` 的行（未应答行权重应为零，会被自动排除）。
    field:
        估计变量；取值为 ``None`` 表示单项未答，仅该行该字段被剔除。
    cluster_field:
        PSU 标识字段，通常为服务点编号。
    confidence:
        区间置信水平，区间依据为 t 分布，自由度为 Σ_h(m_h−1)。
    frame_snapshot:
        抽样设计中的层框规模（含 ``cluster_population``/``cluster_sampled``），
        提供时对单层 PSU 抽样施加有限总体修正。
    kind:
        ``"mean"`` 估计加权均值；``"total"`` 估计总体总量。
    """
    if not 0.0 < confidence < 1.0:
        raise ValueError("置信水平必须位于 (0, 1) 内")
    if kind not in {"mean", "total"}:
        raise ValueError("估计类型只能是 mean 或 total")

    answered = [
        row
        for row in rows
        if row.get("responded") and float(row.get("weight", 0.0)) > 0 and row.get(field) is not None
    ]
    item_missing = sum(
        1
        for row in rows
        if row.get("responded") and row.get(field) is None and float(row.get("weight", 0.0)) > 0
    )
    if not answered:
        return {
            "field": field,
            "kind": kind,
            "domain": domain_label,
            "estimate": None,
            "standard_error": None,
            "confidence_interval": None,
            "degrees_of_freedom": 0,
            "sample_n": 0,
            "psu_count": 0,
            "item_nonresponse_count": item_missing,
            "effective_n": 0,
            "interval_basis": None,
            "limitations": ["该域没有任何带有效取值的应答单位，不做总体外推"],
        }

    total_weight = sum(float(row["weight"]) for row in answered)
    weighted_sum = sum(float(row[field]) * float(row["weight"]) for row in answered)
    mean_point = weighted_sum / total_weight
    point = weighted_sum if kind == "total" else mean_point

    # 按 (层, PSU) 聚合加权指标和与权重和。均值与总量共用同一套 PSU
    # 残差，只在最后按估计量换算，保证 FPC 只施加一次、量纲正确。
    clusters: dict[tuple[str, str], dict[str, float]] = {}
    for row in answered:
        key = _cluster_identity(row, cluster_field)
        bucket = clusters.setdefault(key, {"w": 0.0, "wy": 0.0})
        weight = float(row["weight"])
        bucket["w"] += weight
        bucket["wy"] += weight * float(row[field])

    strata_names = sorted({key[0] for key in clusters})
    psu_counts = {stratum: 0 for stratum in strata_names}
    for stratum, _ in clusters:
        psu_counts[stratum] += 1

    # 均值尺度的 ultimate cluster 方差：
    # V(mean) = Σ_h f_h · m_h/(m_h−1) · Σ_a (wy_ha − mean·w_ha)² / W²
    variance_mean_numerator = 0.0
    singleton_strata: list[str] = []
    for stratum in strata_names:
        keys = [key for key in clusters if key[0] == stratum]
        m_h = len(keys)
        if m_h == 1:
            # 单 PSU 层无法从样本估计层内方差：按惯例贡献零方差并显式标记，
            # 绝不借用其他层数据制造虚假精度。
            singleton_strata.append(stratum)
            continue
        residuals = [
            clusters[key]["wy"] - mean_point * clusters[key]["w"] for key in keys
        ]
        layer_sum = sum(value * value for value in residuals)
        layer_variance = m_h / (m_h - 1) * layer_sum
        fpc = 1.0
        if frame_snapshot and stratum in frame_snapshot:
            info = frame_snapshot[stratum]
            population = info.get("cluster_population")
            sampled = info.get("cluster_sampled", m_h)
            if population and sampled and population > sampled:
                fpc = 1.0 - sampled / population
        variance_mean_numerator += fpc * layer_variance

    if kind == "mean":
        variance = variance_mean_numerator / (total_weight * total_weight)
    else:
        variance = variance_mean_numerator

    degrees_freedom = sum(count - 1 for count in psu_counts.values() if count > 1)
    standard_error = math.sqrt(variance) if variance >= 0 else None
    interval = None
    interval_basis = None
    if standard_error is not None and degrees_freedom > 0:
        critical = float(t_distribution.ppf((1.0 + confidence) / 2.0, degrees_freedom))
        interval = [point - critical * standard_error, point + critical * standard_error]
        interval_basis = (
            f"ultimate cluster 线性化方差，t 分布（自由度 {degrees_freedom}，"
            f"置信水平 {confidence:g}），PSU 字段 {cluster_field}"
        )
        if frame_snapshot is not None:
            interval_basis += "，含有限总体修正 FPC"
    elif standard_error is not None:
        interval_basis = (
            "所有层均只抽中一个 PSU，样本内无法估计抽样方差，"
            "不构造置信区间（禁止退回单位独立假设）"
        )

    squared = sum(float(row["weight"]) ** 2 for row in answered)
    effective_n = total_weight * total_weight / squared if squared else 0.0
    limitations: list[str] = []
    if singleton_strata:
        limitations.append(
            f"层 {singleton_strata} 仅一个抽中服务点，层内方差不可估，"
            "其对总方差的贡献按零处理，区间可能偏窄"
        )
    if item_missing:
        limitations.append(f"字段 {field} 存在 {item_missing} 个单项未答，已按字段剔除且未填零")

    return {
        "field": field,
        "kind": kind,
        "domain": domain_label,
        "estimate": point,
        "standard_error": standard_error,
        "confidence_interval": interval,
        "confidence_level": confidence,
        "degrees_of_freedom": degrees_freedom,
        "sample_n": len(answered),
        "psu_count": len(clusters),
        "item_nonresponse_count": item_missing,
        "effective_n": effective_n,
        "interval_basis": interval_basis,
        "limitations": limitations,
    }
