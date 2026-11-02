# 数据结构与范围

当前审核状态见`data/expert_review_status.json`：维护方于2026-10-27确认数据集已获地学专家审核、没有异议，作为金标使用。规范JSONL中的历史`label_status`及`source.expert_oef_gold`保留原提取阶段记录，不再代表当前是否待审核；最新审核状态优先。不改动原始输入、标签集合、O内容或源指纹，也不编造专家身份、签名或一致性统计。

`data/benchmark.jsonl` 每行是一条样本：

- `task_id`：中性标识。
- `input`：`hypothesis`、`initial_information`、`available_conditions`，各含文本、source_supported状态、证据定位/指纹。不是作者原始预注册T0。
- `source`：论文题录及原案例SHA。可供维护方追溯，不发给受测Agent；题录未标核实不等于本次重新独立核实。
- `reference.class_ids`：该样本所属现有19类的并集，评分真值。
- `reference.O`：只含参与现有19类标签评分的实验操作；每项保留O编号、原描述/目的/材料、类别与理由、源O指纹。不含被否决的新类别提案。
- `reference.omitted_unscored_o_count`：本地同篇档案中未作为本包答案发布的O数量，不是模型需要预测的类别数。
- `eligibility`：evaluable=true，missing_fields与reason_codes为空。

277条记录共1,572项评分O。源案例中的356项支持/范围外O不随答案发布，其存在不等于应新增类别；完整档案仅留维护方本地。22条被否决扩类的完整输入案例不以完整记录混入评测集，只留排除索引。

`data/expert_scope_decision.json`记录维护方转述的专家结论及最后更正：R15实际U–Pb测年O05归M1而非S1；不为光学岩相、CL或Lu–Hf物源分析赋测年标签。不伪造签名、审核名单或所有样本专家批准状态。

原276条输入与参考类别集合未改。此次标签映射和O范围筛选是明确的新版本，应使用新数据/taxonomy哈希，不与旧版分数混用。
