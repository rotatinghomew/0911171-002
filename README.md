# 调查样本分析

容器环境固定在仓库 Dockerfile 的 Python 3.11.14 镜像摘要，数值分析依赖与传递依赖在 requirements.txt 中精确锁定。执行 `docker build -t local-analytics .` 后，`docker run --rm --network none local-analytics` 运行内置例子；`docker run --rm --network none local-analytics python -m unittest discover -s tests -v` 运行测试。构建时安装依赖，运行时无需联网；非容器环境使用 Python 3.11，并先执行 `python -m pip install -r requirements.txt`。

本地合成调查样例用于检视分层与整群抽样下的估计，不含真实受访者资料。项目把抽样设计、回收批次、权重校准、方差估计和发布留痕拆分。

运行 `python -m unittest discover -s tests -v`；运行 `python -m survey.demo` 查看端到端流程。输出只描述合成样本和登记边际，不代表未应答者的真实答案。

## 模块与口径

- `survey.sampling.SampleDesign`
  - `draw`：分层简单随机抽样（历史口径，单位独立），种子与层样本量随设计编号保存，抽中单位带包含概率与基础权重；
  - `draw_clustered`：两阶段分层整群抽样，先抽服务点（PSU）再抽点内单位（SSU），分别记录 `cluster_probability`、`unit_probability`、联合入样概率与 `frame_snapshot`（层/点框规模，供 FPC 使用）。同一服务点内的单位不是独立抽样。
- `survey.batches`：补访名单抽取。支持分层 SRS 配额与逐单位 Poisson 不等概率两种机制，必须显式选择；每个入选单位记录条件入选概率 `phase_probability`。补回应答回指批次，基础权重为原始基础权重除以补访概率，与初始等概率回收口径不同，禁止混用。
- `survey.fieldwork.Fieldwork`：保存抽样单位、回收答案与发布记录。答案不能覆盖抽样设计字段；每个单位只能登记一次整份应答；答案中的 `None` 是单项未答，只在对应字段估计中剔除，绝不填零；整份未回收单位保留在框中、权重为零。
- `survey.benchmark.Benchmark`：多维度人口边际基准。各维度边际总量必须一致，矛盾边际构造时即抛 `ContradictoryMargins`；基准类别在回收中没有样本支持、或应答单位维度取值缺失时抛 `UnsupportedCell`，不做静默修补。
- `survey.weights`
  - `calibrate_weights`：旧版单分层事后分层校准，仅用于历史发布复核；
  - `rake_weights`：多边际迭代加权（IPF/raking），同时对齐地区、年龄等边际；收敛停滞（联合结构零单元格导致边际数学上不可行）时报错并列出空缺联合单元格；
  - `weight_diagnostics`：最大/最小权重、权重比、变异系数、Kish 有效样本量、最大权重份额与极端权重清单。极端权重只**标记**，不修剪不缩尾。
- `survey.estimate.estimate_field`：ultimate cluster 线性化方差，按服务点 PSU 聚合残差，同点单位不被当作独立抽样；自由度为 Σ_h(m_h−1)，区间用 t 分位数；设计提供框规模时施加 FPC；单 PSU 层无法估计层内方差时显式标注且不借造精度；所有层均为单 PSU 时不构造区间。无样本支持的域返回 `estimate=None` 并附限制说明。
- `survey.report`：组装不可变发布记录，包含设计依据、回收批次与补访机制、基准快照、校准设定与收敛诊断、校准前后总体与地区（域）估计、权重集中度、区间依据和全部限制。

## 发布留痕与可重做

`Fieldwork.publish` 的每条发布带唯一编号与内容 SHA-256 指纹；编号不可复用、记录不可变。补回答案或更换人口基准时使用**新发布编号**重做比较，旧发布（`get_release` / `republish_check`）原样可核对。数据不足以支持总体外推时，用 `publish_sample_only` 只发布样本描述并写明限制，不生成人口外推。旧的单层 `release` 接口仍保留，记录中明确标注其口径限制（未对齐年龄边际、方差未尊重整群结构）。
