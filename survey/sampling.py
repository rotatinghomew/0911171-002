from copy import deepcopy
import random


class SampleDesign:
    """抽样框与抽样设计。

    抽样框中的单位可以只带分层（``stratum``），也可以带服务点编号
    （``cluster``）。两类设计语义不同：

    - :meth:`draw`：分层内简单随机抽样，单位彼此独立，入样概率为
      ``n_h / N_h``；
    - :meth:`draw_clustered`：先在层内抽服务点（PSU），再在抽中服务点内
      抽单位（SSU）。同一服务点内的单位不是独立抽样，方差估计必须按
      服务点聚类，不能退回单位独立假设。

    抽中单位始终带 ``inclusion_probability`` 与 ``base_weight``；整群设计
    另保留两级概率，便于复核实际选取机制。
    """

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
        result = {'design_id': design_id, 'design_kind': 'stratified_srs', 'quotas': dict(quotas), 'seed': seed, 'sample': sorted(selected, key=lambda row: row['unit_id'])}
        self.draws[design_id] = deepcopy(result)
        return deepcopy(result)

    def draw_clustered(
        self,
        design_id: str,
        cluster_quotas: dict[str, int],
        unit_quotas: dict[str, int],
        seed: int,
        cluster_field: str = 'cluster',
    ) -> dict:
        """两阶段分层整群抽样。

        ``cluster_quotas`` 给出每层抽中的服务点数量，``unit_quotas`` 给出
        每个抽中服务点内抽取的单位数量（本实现采用等群规模）。层内服务点
        与点内单位均为不放回简单随机抽样，两级概率分别记录：

        - ``cluster_probability`` = 抽中层服务点数 / 层内服务点总数
        - ``unit_probability`` = 点内样本量 / 点内单位总数
        - ``inclusion_probability`` = 两级概率之积
        - ``base_weight`` = 入样概率的倒数

        设计结果同时保存层与服务点的框规模，供方差 FPC 与发布复核使用。
        """
        if design_id in self.draws:
            previous = self.draws[design_id]
            if (
                previous.get('cluster_quotas') != cluster_quotas
                or previous.get('unit_quotas') != unit_quotas
                or previous.get('seed') != seed
                or previous.get('cluster_field') != cluster_field
            ):
                raise ValueError('已执行抽样的设计编号不能换配置')
            return deepcopy(previous)

        strata: dict[str, dict] = {}
        for identifier in sorted(self.frame):
            unit = self.frame[identifier]
            stratum = unit['stratum']
            cluster = unit.get(cluster_field)
            if cluster is None:
                raise ValueError(f"单位 {identifier} 缺少服务点字段 {cluster_field}")
            bucket = strata.setdefault(stratum, {'clusters': {}, 'units': 0})
            bucket['clusters'].setdefault(cluster, []).append(unit)
            bucket['units'] += 1

        if set(cluster_quotas) != set(strata):
            raise ValueError('各层都必须登记服务点样本量')
        if set(unit_quotas) != set(strata):
            raise ValueError('各层都必须登记点内单位样本量')

        generator = random.Random(seed)
        selected: list[dict] = []
        frame_snapshot: dict[str, dict] = {}
        for stratum in sorted(strata):
            bucket = strata[stratum]
            clusters = bucket['clusters']
            cluster_count = cluster_quotas[stratum]
            per_cluster = unit_quotas[stratum]
            total_clusters = len(clusters)
            if (
                not isinstance(cluster_count, int)
                or not isinstance(per_cluster, int)
                or cluster_count <= 0
                or cluster_count > total_clusters
            ):
                raise ValueError('服务点样本量须在一与该层服务点总数之间')
            if per_cluster <= 0:
                raise ValueError('点内单位样本量至少为一')
            smallest = min(len(members) for members in clusters.values())
            if per_cluster > smallest:
                raise ValueError('点内单位样本量不能超过最小服务点的单位数')
            cluster_probability = cluster_count / total_clusters
            chosen_ids = sorted(generator.sample(sorted(clusters), cluster_count))
            frame_snapshot[stratum] = {
                'cluster_population': total_clusters,
                'cluster_sampled': cluster_count,
                'unit_population': bucket['units'],
                'cluster_unit_population': {
                    cluster: len(members) for cluster, members in sorted(clusters.items())
                },
                'sampled_clusters': sorted(chosen_ids),
            }
            for cluster in chosen_ids:
                members = clusters[cluster]
                unit_probability = per_cluster / len(members)
                inclusion = cluster_probability * unit_probability
                for unit in generator.sample(members, per_cluster):
                    selected.append({
                        **deepcopy(unit),
                        'cluster_probability': cluster_probability,
                        'unit_probability': unit_probability,
                        'inclusion_probability': inclusion,
                        'base_weight': 1.0 / inclusion,
                    })

        result = {
            'design_id': design_id,
            'design_kind': 'stratified_two_stage_cluster',
            'cluster_field': cluster_field,
            'cluster_quotas': dict(cluster_quotas),
            'unit_quotas': dict(unit_quotas),
            'seed': seed,
            'frame_snapshot': frame_snapshot,
            'sample': sorted(selected, key=lambda row: row['unit_id']),
        }
        self.draws[design_id] = deepcopy(result)
        return deepcopy(result)
