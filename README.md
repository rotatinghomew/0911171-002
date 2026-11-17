# 调查样本分析

容器环境固定在仓库 Dockerfile 的 Python 3.11.14 镜像摘要，数值分析依赖与传递依赖在 requirements.txt 中精确锁定。执行 `docker build -t local-analytics .` 后，`docker run --rm --network none local-analytics` 运行内置例子；`docker run --rm --network none local-analytics python -m unittest discover -s tests -v` 运行测试。构建时安装依赖，运行时无需联网；非容器环境使用 Python 3.11，并先执行 `python -m pip install -r requirements.txt`。

本地合成调查样例用于检视分层抽样下的估计，不含真实受访者资料。项目将权重校准、估计和简报输出拆分。

运行 `python -m unittest discover -s tests -v`；运行 `python -m survey.demo` 查看总体与分层摘要。输出只描述观测样本和当前边际基准，不代表未应答者的真实答案。

`survey.fieldwork.Fieldwork` 保存抽样单位和回收答案，答案不能覆盖抽样层、基础权重等设计字段。未回收单位仍留在抽样框中；回答某项为空与整份未应答不同。当前 `release` 按单一分层总体边际校准，返回当前回收结构的均值摘要，不包含整群抽样的设计方差。基础权重表示入样概率的倒数，不能用最终权重直接解释实际样本人数。

`survey.sampling.SampleDesign.draw` 按完整单位框执行分层简单随机抽样，种子和层样本量随设计编号保存，抽中的单位带包含概率和基础权重。`Fieldwork.from_design` 继承该设计，估计发布中保留原抽样依据。当前设计不支持分服务点整群或按回收状况补访，不能把此处分层独立抽样的含义移用于其他设计。
