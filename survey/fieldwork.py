from copy import deepcopy

from .report import summarize
from .weights import calibrate_weights


class Fieldwork:
    def __init__(self, sample: list[dict]) -> None:
        self.sample: dict[str, dict] = {}
        self.answers: dict[str, dict] = {}
        self.design: dict | None = None
        for unit in sample:
            identifier = unit["unit_id"]
            if identifier in self.sample or float(unit["base_weight"]) <= 0:
                raise ValueError("样本编号必须唯一且基础权重为正")
            self.sample[identifier] = deepcopy(unit)

    @classmethod
    def from_design(cls, design: dict):
        fieldwork = cls(design['sample'])
        fieldwork.design = deepcopy(design)
        return fieldwork

    def respond(self, unit_id: str, answers: dict) -> None:
        if unit_id not in self.sample:
            raise ValueError("应答单位不在抽样框中")
        protected = {"unit_id", "stratum", "base_weight", "responded", "weight"} | set(self.sample[unit_id])
        if protected.intersection(answers):
            raise ValueError("答案不得修改抽样设计")
        self.answers[unit_id] = deepcopy(answers)

    def response_rows(self) -> list[dict]:
        return [
            {**deepcopy(unit), **deepcopy(self.answers.get(identifier, {})), "responded": identifier in self.answers}
            for identifier, unit in sorted(self.sample.items())
        ]

    def release(self, margins: dict[str, float], field: str) -> dict:
        rows = calibrate_weights(self.response_rows(), margins)
        return {"design": deepcopy(self.design), "margins": deepcopy(margins), "field": field, "summary": summarize(rows, field)}
