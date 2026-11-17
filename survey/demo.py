"""端到端合成示例：地区服务使用情况简报的可复核估计流程。

数据全部为本地合成，不含真实受访者。流程演示：

1. 构造带层（地区）与服务点（PSU）的抽样框，执行两阶段整群抽样；
2. 初始回收明显偏向城区，并制造整份未回收与单项未答；
3. 按当前回收结构抽取补访名单（记录机制与入选概率），补回应答回到
   对应批次，不与初始等概率回收混用；
4. 用同时含地区、年龄边际的人口基准做 raking 校准，发布校准前后的
   总体/地区估计、权重集中度与整群稳健区间；
5. 演示矛盾边际、无支持单元格、极端权重不被静默抹平；
6. 更换人口基准后以新发布编号重做，旧发布原样可核对；
7. 数据不足以支持外推时，只发布样本描述并写明限制。
"""
import random

from .batches import INITIAL_BATCH
from .benchmark import Benchmark, ContradictoryMargins, UnsupportedCell
from .fieldwork import Fieldwork
from .sampling import SampleDesign

REGIONS = ["城区", "郊县"]
AGE_GROUPS = ["青年", "中年", "老年"]
SERVICES = ["政务", "医疗", "就业"]


def build_frame() -> list[dict]:
    """合成抽样框：2 个地区层 × 每地区 6 个服务点 × 每点 25 个单位。"""
    frame = []
    identifier = 0
    for region in REGIONS:
        for point in range(6):
            cluster = f"{region}-点{point + 1}"
            for _ in range(25):
                identifier += 1
                # 框内年龄结构：城区偏年轻，郊县偏年长，制造校准空间。
                age = random.Random(identifier).choice(
                    ["青年", "青年", "中年"] if region == "城区" else ["中年", "老年", "老年"]
                )
                frame.append({
                    "unit_id": f"u{identifier:03d}",
                    "stratum": region,
                    "cluster": cluster,
                    "age_group": age,
                    "registered_service": random.Random(identifier * 7 + 3).choice(SERVICES),
                })
    return frame


def true_value(row: dict) -> float:
    """合成的“月均服务使用小时数”生成机制。"""
    base = {"城区": 9.0, "郊县": 5.0}[row["stratum"]]
    age_effect = {"青年": 1.5, "中年": 0.0, "老年": -1.0}[row["age_group"]]
    noise = random.Random(hash(row["unit_id"]) & 0xFFFF).uniform(-1.2, 1.2)
    return round(base + age_effect + noise, 2)


def main() -> None:
    random.seed(20261117)
    frame = build_frame()
    design = SampleDesign(frame)
    drawn = design.draw_clustered(
        "d2026w47",
        cluster_quotas={"城区": 4, "郊县": 2},
        unit_quotas={"城区": 8, "郊县": 8},
        seed=20261117,
    )
    print(f"抽中单位 {len(drawn['sample'])} 个，设计：{drawn['design_kind']}")

    fieldwork = Fieldwork.from_design(drawn)

    # 初始回收：城区高应答、郊县低应答；少量单项未答。
    initial_respondents = []
    for row in drawn["sample"]:
        propensity = 0.85 if row["stratum"] == "城区" else 0.35
        if random.random() < propensity:
            hours = true_value(row)
            answer = {"service_hours": hours, "satisfaction": round(random.uniform(1, 5), 1)}
            if random.random() < 0.12:
                answer["satisfaction"] = None  # 单项未答，绝不填零
            fieldwork.respond(row["unit_id"], answer, batch_id=INITIAL_BATCH)
            initial_respondents.append(row["unit_id"])
    print(f"初始批次应答 {len(initial_respondents)} 个")

    # 人口基准：地区与年龄边际总量一致（各 300），但结构与回收偏差不同。
    benchmark_v1 = Benchmark(
        name="地区人口基准 v1",
        margins={
            "地区": {"城区": 180, "郊县": 120},
            "年龄": {"青年": 120, "中年": 105, "老年": 75},
        },
        source="合成登记人口（示例）",
        as_of="2026-11-01",
    )

    # 第一次发布：仅初始回收，先看偏差与权重集中程度。
    first = fieldwork.publish(
        "r2026w47-initial",
        benchmark_v1,
        fields=["service_hours", "satisfaction"],
        dimension_fields={"地区": "stratum", "年龄": "age_group"},
        domain_field="stratum",
        note="初始回收简报，城区应答占比偏高",
    )
    _print_release(first, title="发布一：初始回收 + 双边际 raking")

    # 补访：按回收结构对未应答单位分层抽 60%，记录条件入选概率。
    plan = fieldwork.draw_followup(
        "followup-1",
        seed=4242,
        quotas={
            region: max(1, round(
                sum(1 for row in fieldwork.response_rows()
                    if not row["responded"] and row["stratum"] == region) * 0.6
            ))
            for region in REGIONS
        },
    )
    print(f"补访名单 {len(plan['roster'])} 个，机制：{plan['mechanism']['kind']}")
    for row in plan["roster"]:
        source = next(item for item in drawn["sample"] if item["unit_id"] == row["unit_id"])
        fieldwork.respond(
            row["unit_id"],
            {"service_hours": true_value(source), "satisfaction": round(random.uniform(1, 5), 1)},
            batch_id="followup-1",
        )

    # 第二次发布：补回答案回到补访批次，联合入样概率进入权重。
    second = fieldwork.publish(
        "r2026w47-after-followup",
        benchmark_v1,
        fields=["service_hours", "satisfaction"],
        dimension_fields={"地区": "stratum", "年龄": "age_group"},
        domain_field="stratum",
        note="补访批次并入后的复核发布",
    )
    _print_release(second, title="发布二：补访后重做（批次权重不混用）")

    # 旧发布仍可原样核对，指纹不变。
    assert fieldwork.get_release("r2026w47-initial")["fingerprint"] == first["fingerprint"]
    print("旧发布指纹保持不变，早先简报可核对")

    # 更换人口基准，新编号重做比较。
    benchmark_v2 = Benchmark(
        name="地区人口基准 v2",
        margins={
            "地区": {"城区": 165, "郊县": 135},
            "年龄": {"青年": 105, "中年": 110, "老年": 85},
        },
        source="合成登记人口（示例，修订版）",
        as_of="2026-11-15",
    )
    third = fieldwork.publish(
        "r2026w47-benchmark-v2",
        benchmark_v2,
        fields=["service_hours"],
        dimension_fields={"地区": "stratum", "年龄": "age_group"},
        domain_field="stratum",
        note="换人口基准后的重做发布",
    )
    print(f"换基准后 service_hours 总体估计："
          f"{third['estimates']['after_calibration']['service_hours']['overall']['mean']['estimate']:.3f}")

    # 失败路径一：矛盾边际直接拒绝。
    try:
        Benchmark(
            name="矛盾基准",
            margins={"地区": {"城区": 180, "郊县": 120}, "年龄": {"青年": 90, "中年": 90, "老年": 90}},
        )
    except ContradictoryMargins as exc:
        print(f"矛盾边际被拒绝：{exc}")

    # 失败路径二：基准类别没有样本支持，禁止抹平。
    bad_frame = [dict(row, age_group="老年" if row["age_group"] == "青年" else row["age_group"]) for row in frame]
    bad_design = SampleDesign(bad_frame).draw_clustered(
        "d-bad", {"城区": 4, "郊县": 2}, {"城区": 8, "郊县": 8}, seed=7
    )
    bad_field = Fieldwork.from_design(bad_design)
    for row in bad_design["sample"]:
        bad_field.respond(row["unit_id"], {"service_hours": 3.0})
    try:
        bad_field.publish(
            "r-bad",
            Benchmark("无青年基准", {"地区": {"城区": 180, "郊县": 120}, "年龄": {"青年": 60, "中年": 120, "老年": 120}}),
            fields=["service_hours"],
            dimension_fields={"地区": "stratum", "年龄": "age_group"},
        )
    except UnsupportedCell as exc:
        print(f"无支持单元格被拒绝：{exc}")

    # 兜底路径：不支持外推时只留样本描述与限制说明。
    fallback = bad_field.publish_sample_only(
        "r-bad-fallback",
        fields=["service_hours"],
        reason="回收样本缺少青年类别，无法对齐年龄边际",
        domain_field="stratum",
    )
    print(f"兜底发布类型：{fallback['release_kind']}；限制 {len(fallback['limitations'])} 条")

    # 极端权重只标记不平抹：查看第二次发布的权重诊断。
    diag = second["calibration_diagnostics"]
    print(
        "权重诊断：有效样本量 {eff:.1f}，最大/最小比 {ratio:.2f}，"
        "极端权重 {n} 个（仅标记）".format(
            eff=diag["effective_sample_size"],
            ratio=diag["weight_ratio"],
            n=len(diag["extreme_weights"]),
        )
    )


def _print_release(record: dict, title: str) -> None:
    print(f"\n=== {title}（{record['release_id']}）===")
    for stage in ("before_calibration", "after_calibration"):
        block = record["estimates"][stage]["service_hours"]
        overall = block["overall"]["mean"]
        ci = overall["confidence_interval"]
        ci_text = f"[{ci[0]:.2f}, {ci[1]:.2f}]" if ci else "不可构造"
        print(
            f"[{stage}] 总体均值 {overall['estimate']:.3f}，SE {overall['standard_error']:.3f}，"
            f"95%CI {ci_text}，PSU {overall['psu_count']} 个，自由度 {overall['degrees_of_freedom']}"
        )
        for domain, estimate in sorted(block["domains"].items()):
            mean = estimate["mean"]
            print(f"    地区 {domain}：均值 {mean['estimate']:.3f}，SE {mean['standard_error']:.3f}")
    print(f"区间依据：{record['estimates']['after_calibration']['service_hours']['overall']['mean']['interval_basis']}")
    print(f"批次结构：{record['batches']['respondents_by_batch']}")


if __name__ == "__main__":
    main()
