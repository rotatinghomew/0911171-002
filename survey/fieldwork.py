"""实地回收与发布管理。

设计原则
========
- 抽样设计字段（层、服务点、入样概率、基础权重、批次信息）受保护，
  答案无法覆盖；未回收单位始终留在抽样框中。
- 整份未回收（无答案记录）与单项未答（已应答但某字段为 ``None``）严格
  区分，任何估计都不把未答填成零。
- 补访名单来自 :mod:`survey.batches`，补回应答必须回指批次编号；初始
  批次与补访批次的权重口径不同，发布中逐单位保留批次与入选概率。
- 发布记录不可变：同一发布编号重复提交必须内容一致（指纹幂等）；补回
  答案或更换人口基准后使用新发布编号重做比较，旧简报仍可原样核对。
"""
import hashlib
import json
from copy import deepcopy

from .batches import INITIAL_BATCH, FollowUpError, draw_followup
from .benchmark import Benchmark
from .report import build_release, sample_description
from .weights import calibrate_weights

_PROTECTED_FIELDS = {
    "unit_id",
    "stratum",
    "cluster",
    "base_weight",
    "inclusion_probability",
    "cluster_probability",
    "unit_probability",
    "phase_probability",
    "response_batch",
    "responded",
    "weight",
    "phase_weight",
}


class ReleaseError(ValueError):
    """发布流程不合法（编号冲突、批次未登记等）。"""


class Fieldwork:
    def __init__(self, sample: list[dict]) -> None:
        self.sample: dict[str, dict] = {}
        self.answers: dict[str, dict] = {}
        self.response_batches: dict[str, str] = {}
        self.design: dict | None = None
        self.followups: dict[str, dict] = {}
        self.releases: dict[str, dict] = {}
        for unit in sample:
            identifier = unit["unit_id"]
            if identifier in self.sample or float(unit["base_weight"]) <= 0:
                raise ValueError("样本编号必须唯一且基础权重为正")
            self.sample[identifier] = deepcopy(unit)

    @classmethod
    def from_design(cls, design: dict):
        fieldwork = cls(design["sample"])
        fieldwork.design = deepcopy(design)
        return fieldwork

    # ------------------------------------------------------------------ 回收

    def respond(self, unit_id: str, answers: dict, batch_id: str = INITIAL_BATCH) -> None:
        """登记一份应答。

        每个单位只能登记一次整份应答（补访补回也只产生一份，标注补访
        批次）；重复登记直接报错，保证发布可复核。答案中的 ``None`` 表示
        单项未答，原样保留。
        """
        if unit_id not in self.sample:
            raise ValueError("应答单位不在抽样框中")
        if unit_id in self.answers:
            raise ReleaseError(
                f"单位 {unit_id} 已在批次 {self.response_batches[unit_id]} 应答，不能覆盖"
            )
        protected = _PROTECTED_FIELDS | set(self.sample[unit_id])
        if protected.intersection(answers):
            raise ValueError("答案不得修改抽样设计")
        if batch_id != INITIAL_BATCH and batch_id not in self.followups:
            raise FollowUpError(f"批次 {batch_id} 未登记补访名单，不能接收应答")
        if batch_id != INITIAL_BATCH:
            rostered = {row["unit_id"] for row in self.followups[batch_id]["roster"]}
            if unit_id not in rostered:
                raise FollowUpError(f"单位 {unit_id} 不在批次 {batch_id} 的补访名单中")
        self.answers[unit_id] = deepcopy(answers)
        self.response_batches[unit_id] = batch_id

    def draw_followup(self, batch_id: str, seed: int, **kwargs) -> dict:
        """按当前回收结构抽取补访名单，并保存实际选取机制与入选概率。"""
        if batch_id in self.followups:
            raise ReleaseError(f"补访批次 {batch_id} 已存在，不能重复抽取")
        plan = draw_followup(batch_id, self.response_rows(), seed, **kwargs)
        self.followups[batch_id] = plan
        return deepcopy(plan)

    def response_rows(self) -> list[dict]:
        rows = []
        for identifier, unit in sorted(self.sample.items()):
            row = {**deepcopy(unit), "responded": identifier in self.answers}
            if identifier in self.answers:
                row.update(deepcopy(self.answers[identifier]))
                row["response_batch"] = self.response_batches[identifier]
                batch = self.response_batches[identifier]
                if batch != INITIAL_BATCH:
                    row["phase_probability"] = self._phase_probability(identifier, batch)
                else:
                    row["phase_probability"] = 1.0
            rows.append(row)
        return rows

    def _phase_probability(self, unit_id: str, batch_id: str) -> float:
        for row in self.followups[batch_id]["roster"]:
            if row["unit_id"] == unit_id:
                return float(row["phase_probability"])
        raise FollowUpError(f"单位 {unit_id} 不在批次 {batch_id} 名单中")

    # ------------------------------------------------------------------ 发布

    def publish(
        self,
        release_id: str,
        benchmark: Benchmark,
        fields: list[str],
        dimension_fields: dict[str, str],
        *,
        confidence: float = 0.95,
        extreme_ratio: float = 10.0,
        max_iterations: int = 500,
        domain_field: str | None = None,
        note: str | None = None,
    ) -> dict:
        """按人口基准生成不可变发布记录。

        校准失败（矛盾边际、无支持单元格、不收敛等）以异常上抛，不会产出
        带静默修补的发布；需要在数据不足时留底，请改用
        :meth:`publish_sample_only`。
        """
        self._check_release_id(release_id)
        record = build_release(
            release_id=release_id,
            design=self.design,
            rows=self.response_rows(),
            benchmark=benchmark,
            fields=fields,
            dimension_fields=dimension_fields,
            followups=self.followups,
            confidence=confidence,
            extreme_ratio=extreme_ratio,
            max_iterations=max_iterations,
            domain_field=domain_field,
            note=note,
        )
        self._store_release(release_id, record)
        return self.get_release(release_id)

    def publish_sample_only(
        self,
        release_id: str,
        fields: list[str],
        *,
        reason: str,
        confidence: float = 0.95,
        domain_field: str | None = None,
        note: str | None = None,
    ) -> dict:
        """数据不能支持总体外推时的样本描述发布。

        只报告观测样本（等权描述与按基础权重的设计描述），明确标注限制，
        不生成任何人口总量外推或校准权重。
        """
        self._check_release_id(release_id)
        record = sample_description(
            release_id=release_id,
            design=self.design,
            rows=self.response_rows(),
            fields=fields,
            followups=self.followups,
            reason=reason,
            confidence=confidence,
            domain_field=domain_field,
            note=note,
        )
        self._store_release(release_id, record)
        return self.get_release(release_id)

    def release(self, margins: dict[str, float], field: str) -> dict:
        """旧版单层校准发布，保留历史口径；新工作请使用 :meth:`publish`。"""
        release_id = f"legacy-{field}-{len(self.releases)}"
        self._check_release_id(release_id)
        rows = calibrate_weights(self.response_rows(), margins)
        from .report import legacy_release

        record = legacy_release(release_id, self.design, rows, margins, field)
        self._store_release(release_id, record)
        return self.get_release(release_id)

    def get_release(self, release_id: str) -> dict:
        if release_id not in self.releases:
            raise ReleaseError(f"发布编号 {release_id} 不存在")
        return deepcopy(self.releases[release_id])

    def _check_release_id(self, release_id: str) -> None:
        if not release_id:
            raise ReleaseError("发布编号不能为空")
        if release_id in self.releases:
            raise ReleaseError(
                f"发布编号 {release_id} 已存在且不可变；重做比较请使用新编号"
            )

    def _store_release(self, release_id: str, record: dict) -> None:
        record = deepcopy(record)
        record["fingerprint"] = fingerprint(record)
        self.releases[release_id] = record

    def republish_check(self, release_id: str, candidate: dict) -> bool:
        """核对候选记录与已存发布的指纹是否一致（用于回放复核）。"""
        if release_id not in self.releases:
            raise ReleaseError(f"发布编号 {release_id} 不存在")
        stored = deepcopy(self.releases[release_id])
        stored_fp = stored.pop("fingerprint")
        candidate = deepcopy(candidate)
        candidate_fp = candidate.pop("fingerprint", fingerprint(candidate))
        return stored_fp == candidate_fp and fingerprint(stored) == fingerprint(candidate)


def fingerprint(record: dict) -> str:
    """对发布内容（不含指纹本身）计算稳定 SHA-256 指纹。"""
    payload = json.dumps(record, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
