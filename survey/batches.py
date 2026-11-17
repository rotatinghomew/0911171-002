"""补访（追访）批次的名单抽取。

初始抽样与补访是两个抽样阶段，选取机制不同，不能把补回应答混成
最初等概率回收。每个补访批次都逐单位保存：

- ``phase_probability``：本批次内被选入补访名单的条件概率（给定仍未应答）；
- ``mechanism``：实际使用的选取机制与参数；
- 名单在 :class:`~survey.fieldwork.Fieldwork` 应答时必须回指本批次编号，
  该批次的二阶段权重为 ``base_weight / phase_probability``。
"""
from copy import deepcopy
import math
import random

INITIAL_BATCH = 'initial'


class FollowUpError(ValueError):
    """补访名单抽取不合法。"""


def _eligible_nonrespondents(rows: list[dict]) -> list[dict]:
    return [row for row in rows if not row.get('responded')]


def draw_followup(
    batch_id: str,
    rows: list[dict],
    seed: int,
    quotas: dict[str, int] | None = None,
    probs: dict[str, float] | None = None,
    strata_field: str = 'stratum',
) -> dict:
    """从当前仍未整份应答的单位中抽取补访名单。

    两种互斥机制，必须显式选择其一，禁止使用隐式默认：

    - 分层简单随机（给出 ``quotas``，每层抽多少个）：层内不放回 SRS，
      入选概率为 ``m_h / R_h``，其中 ``R_h`` 为该层未应答单位数；
    - Poisson 不等概率（给出 ``probs``，每个单位一个条件入选概率）：
      逐单位独立 Bernoulli 抽取，名单量随机，概率逐单位记录。

    已经应答（含更早批次补回）的单位不在候选池中；候选池为空或层内
    配额超出未应答数都会报错，而不是悄悄放宽。
    """
    if not batch_id or batch_id == INITIAL_BATCH:
        raise FollowUpError('补访批次编号不能为空且不能占用初始批次名')
    if (quotas is None) == (probs is None):
        raise FollowUpError('必须且只能指定一种补访机制：分层配额或逐单位概率')
    candidates = _eligible_nonrespondents(rows)
    if not candidates:
        raise FollowUpError('没有未应答单位，无法抽取补访名单')

    generator = random.Random(seed)
    roster: list[dict] = []

    if quotas is not None:
        groups: dict[str, list[dict]] = {}
        for row in candidates:
            groups.setdefault(row[strata_field], []).append(row)
        if set(quotas) != set(groups):
            raise FollowUpError('各含未应答单位的层都必须登记补访配额')
        mechanism = {
            'kind': 'stratified_srs',
            'strata_field': strata_field,
            'quotas': dict(quotas),
        }
        for stratum in sorted(groups):
            pool = sorted(groups[stratum], key=lambda row: row['unit_id'])
            quota = quotas[stratum]
            if not isinstance(quota, int) or quota <= 0 or quota > len(pool):
                raise FollowUpError(
                    f'层 {stratum} 补访配额须在一与未应答单位数 {len(pool)} 之间'
                )
            phase_probability = quota / len(pool)
            for row in generator.sample(pool, quota):
                roster.append(_roster_row(row, phase_probability))
    else:
        if set(probs) != {row['unit_id'] for row in candidates}:
            raise FollowUpError('必须为每个未应答单位逐一指定入选概率')
        mechanism = {'kind': 'poisson_bernoulli', 'probs': dict(probs)}
        for row in sorted(candidates, key=lambda item: item['unit_id']):
            probability = float(probs[row['unit_id']])
            if not math.isfinite(probability) or not 0.0 < probability <= 1.0:
                raise FollowUpError(
                    f"单位 {row['unit_id']} 的补访概率必须在 (0, 1] 内"
                )
            if generator.random() < probability:
                roster.append(_roster_row(row, probability))
        if not roster:
            raise FollowUpError('Poisson 抽取结果为空名单，请检查概率或随机种子')

    roster.sort(key=lambda row: row['unit_id'])
    return {
        'batch_id': batch_id,
        'seed': seed,
        'mechanism': mechanism,
        'eligible_count': len(candidates),
        'roster': roster,
    }


def _roster_row(row: dict, phase_probability: float) -> dict:
    return {
        'unit_id': row['unit_id'],
        'stratum': row['stratum'],
        'cluster': row.get('cluster'),
        'base_weight': float(row['base_weight']),
        'phase_probability': phase_probability,
    }


def phase_base_weight(row: dict) -> float:
    """带批次的最终入样基础权重。

    初始批次单位：原始基础权重（入样概率倒数）。
    补访批次单位：原始基础权重除以该批次条件入选概率，即两阶段
    联合入样概率的倒数，与初始批次权重口径不同，不得混用。
    """
    base = float(row['base_weight'])
    batch = row.get('response_batch', INITIAL_BATCH)
    if batch == INITIAL_BATCH:
        return base
    probability = row.get('phase_probability')
    if probability is None or not 0.0 < float(probability) <= 1.0:
        raise FollowUpError(
            f"单位 {row['unit_id']} 的补访批次 {batch} 缺少有效入选概率"
        )
    return base / float(probability)
