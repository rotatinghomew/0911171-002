"""人口基准：多维边际及其一致性校验。

基准只描述外部人口总量，不接触回收答案；相互矛盾的边际在构造时即拒绝，
不允许留给后续算法“自动调和”。
"""
import math
from copy import deepcopy


class BenchmarkError(ValueError):
    """基准不合法或无法支持校准时抛出。"""


class ContradictoryMargins(BenchmarkError):
    """不同维度边际的总量互相矛盾。"""


class UnsupportedCell(BenchmarkError):
    """基准要求的单元格在回收样本中没有任何支持。"""


class Benchmark:
    """多维人口边际基准。

    margins 形如 ``{"地区": {"城区": 100, ...}, "年龄": {"青年": 120, ...}}``。
    每个维度各类别人口数必须为正且有限；各维度边际总量必须一致，否则
    抛出 :class:`ContradictoryMargins`。
    """

    def __init__(
        self,
        name: str,
        margins: dict[str, dict[str, float]],
        source: str | None = None,
        as_of: str | None = None,
        tolerance: float = 1e-9,
    ) -> None:
        if not isinstance(margins, dict) or not margins:
            raise BenchmarkError("基准至少包含一个维度的边际")
        cleaned: dict[str, dict[str, float]] = {}
        totals: dict[str, float] = {}
        for dimension, categories in margins.items():
            if not isinstance(categories, dict) or not categories:
                raise BenchmarkError(f"维度 {dimension} 至少包含一个类别")
            bucket: dict[str, float] = {}
            for category, count in categories.items():
                value = float(count)
                if not math.isfinite(value) or value <= 0:
                    raise BenchmarkError(f"边际 {dimension}/{category} 必须为正有限值")
                bucket[str(category)] = value
            cleaned[str(dimension)] = bucket
            totals[str(dimension)] = sum(bucket.values())
        head = next(iter(totals.values()))
        for dimension, total in totals.items():
            if abs(total - head) > tolerance * max(1.0, abs(head)):
                raise ContradictoryMargins(
                    f"维度 {dimension} 边际总量 {total:g} 与首维度总量 {head:g} 不一致"
                )
        self.name = str(name)
        self.margins = cleaned
        self.source = source
        self.as_of = as_of
        self.tolerance = tolerance
        self.population_total = head

    def to_record(self) -> dict:
        """可写入发布记录的可序列化快照。"""
        return {
            "name": self.name,
            "source": self.source,
            "as_of": self.as_of,
            "tolerance": self.tolerance,
            "population_total": self.population_total,
            "margins": deepcopy(self.margins),
        }

    def check_support(self, rows: list[dict], dimension_fields: dict[str, str]) -> None:
        """校验每个基准类别在回收行中都有样本支持。

        - 基准类别没有任何回收单位：:class:`UnsupportedCell`
        - 回收单位在基准维度上取值缺失：同样拒绝，禁止悄悄丢弃或抹平
        """
        respondents = [row for row in rows if row.get("responded")]
        for dimension, categories in self.margins.items():
            field = dimension_fields.get(dimension)
            if not field:
                raise BenchmarkError(f"维度 {dimension} 缺少样本字段映射")
            present: dict[str, int] = {}
            missing_units: list[str] = []
            for row in respondents:
                value = row.get(field)
                if value is None:
                    missing_units.append(row["unit_id"])
                    continue
                present[str(value)] = present.get(str(value), 0) + 1
            if missing_units:
                raise UnsupportedCell(
                    f"维度 {dimension}（字段 {field}）存在取值缺失的应答单位，"
                    f"不能静默剔除：{sorted(missing_units)}"
                )
            unsupported = [category for category in categories if category not in present]
            if unsupported:
                raise UnsupportedCell(
                    f"基准类别在回收样本中没有任何支持，禁止抹平："
                    f"维度 {dimension} 类别 {unsupported}"
                )
