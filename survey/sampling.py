from copy import deepcopy
import random


class SampleDesign:
    def __init__(self, frame: list[dict]) -> None:
        self.frame = {unit['unit_id']: deepcopy(unit) for unit in frame}
        if len(self.frame) != len(frame):
            raise ValueError('抽样框单位编号重复')
        self.draws: dict[str, dict] = {}

    def draw(self, design_id: str, quotas: dict[str, int], seed: int) -> dict:
        if design_id in self.draws:
            previous = self.draws[design_id]
            if previous['quotas'] != quotas or previous['seed'] != seed:
                raise ValueError('已执行抽样的设计编号不能换配置')
            return deepcopy(previous)
        groups: dict[str, list[dict]] = {}
        for identifier in sorted(self.frame):
            unit = self.frame[identifier]
            groups.setdefault(unit['stratum'], []).append(unit)
        if set(quotas) != set(groups):
            raise ValueError('各层都必须登记样本量')
        generator = random.Random(seed)
        selected = []
        for stratum, units in sorted(groups.items()):
            count = quotas[stratum]
            if not isinstance(count, int) or count <= 0 or count > len(units):
                raise ValueError('分层样本量须在一与该层总体数之间')
            for unit in generator.sample(units, count):
                selected.append({**deepcopy(unit), 'base_weight': len(units) / count, 'inclusion_probability': count / len(units)})
        result = {'design_id': design_id, 'quotas': dict(quotas), 'seed': seed, 'sample': sorted(selected, key=lambda row: row['unit_id'])}
        self.draws[design_id] = deepcopy(result)
        return deepcopy(result)
