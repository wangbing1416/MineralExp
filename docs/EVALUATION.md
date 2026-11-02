# 评分协议 / Evaluation protocol

## 分类评分与文本留存分离

v2 Harness要求模型输出完整19类决策，并为选中类提供实验设计、选择理由、预期证据及支持／削弱假说的条件。运行器会保存这些内容和实际请求／原始响应；**本评分器仍只读取`task_id`和`labels`，不读取`class_predictions`、实验文本、rationale或plan来算分**。相同标签对应任意不同说明文本，所有分类指标均相同。详见[输出协议](HARNESS.md)。

运行器对必填字段、布尔类型、19类完整性以及标签一致性的检查只是协议校验，不是LLM-as-a-Judge或科学质量评分。当前没有自动文本裁判，也不把完整解释当作额外正确标签。历史labels-only预测仍可离线计分。新增文字专供之后的Judge和错误分析，不能将其存在误报为已验证的方案质量。

## 完整指标输出

`geotask19.metrics.v2` 在 `overall` 中报告全部19类的指标，在 `per_process` 中报告源运储变保五组的相同指标：

| 字段 | 定义 |
| --- | --- |
| `acc` | 多标签集合完全匹配率（subset accuracy），不是逐标签二元准确率 |
| `precision` | 组内各细分类汇总TP/FP后计算的micro precision |
| `recall` | 组内各细分类汇总TP/FN后计算的micro recall |
| `micro_f1` | 组内汇总TP/FP/FN后计算的F1；原有`f1`为同值别名 |
| `macro_f1` | 分别计算组内每个细分类的F1，再按固定类别数等权平均 |

分组：`Source`为S1–S4（源），`Transport`为T1–T5（运），`Deposition`为D1–D4（储/沉淀富集），`Modification`为M1–M3（变），`Preservation`为P1–P3（保）。这是对19个细分类的分组评测，**不把一组压成单一二元标签**。

各组保留全部277条案例，只投影类别列；参考中没有该组类别的案例也参与，避免忽略误报。组内参考与预测同为空集时，acc计为正确，但precision/recall/F1除零取0。报告`positive_samples`（该组有参考正例的案例数）、`n_supported_labels`及`support`（正例样本–类别对数），帮助识别空集匹配和类别稀疏对分数的影响。

固定MacroF1包含零正例类别；如S4无正例且无预测，其F1为0。因此即使全部标签预测正确，整体固定19类MacroF1也为18/19，而不是1。`macro_supported_classes_only.f1`另外给出仅有正例类别的平均，不能将二者混用。

`process_average`提供五个过程对应指标的等权平均，**不是**整体19类的micro或macro指标。各组类别数不同，五组MacroF1等权平均一般不等于全部19类MacroF1。所有指标为0–1比例。

原顶层`subset_accuracy`、`micro`、`macro`、`macro_supported_classes_only`、逐类及逐样本指标保留。命令行stdout和`--output`指定的JSON均包含`overall`、`per_process`及`process_average`，无需额外分类后重复运行。

## 自动落盘与复算 / Artifact export and replay

运行器完成后自动评分，无需再手动运行评分命令。默认保存到`OUTPUT.artifacts/scores/`：完整`metrics.json`，`overall.csv`（含五过程等权平均行）、`per_process.csv`、`per_class.csv`、`per_sample.csv`，逐样本漏报／多报的`errors.jsonl`，`gold_labels.jsonl`、完整`predictions.jsonl`和带数据／代码哈希的`provenance.json`。CSV使用UTF-8 BOM，列表和嵌套对象以JSON单元格保存。金标快照只用于本地评分复算，不会追加到模型请求中。

独立`python -m geotask19.metrics --data ... --predictions ... --output results/metrics.json`命令仍可使用，并额外生成`results/metrics.json.artifacts/`的同一套评分文件，可用`--artifacts-dir`指定新目录。它只复算分类分数，不会运行模型或重建工具轨迹。输入无效或严格漏答检查失败时保存`scoring_error.json`，不输出伪造的分数；已有路径拒绝覆盖。

失败／中断的运行器会把诊断另存`OUTPUT.artifacts/scores_partial/`，保留完整计划样本数并明确用`missing=empty`计算，将未完成的ID全部列出。它不是完成全库推理的成绩；默认严格评分规则并未因此放宽，独立评分器默认仍为`--missing error`。`provenance.json`明确标注`partial_run`，`run.json`提供计划、尝试、成功、失败和待执行数量。详见[轨迹与保存约定](HARNESS.md)。

The runner automatically writes complete score JSON, overall/process/class/sample CSVs, error cases, replayable gold-label and full-prediction snapshots, provenance and checksums. The standalone scorer exports the same bundle alongside its requested output and never regenerates model/tool traces. Incomplete runner attempts are clearly separated under `scores_partial/`, using all scheduled rows with explicit missing=empty diagnostics and missing IDs. They are not completed full-run results; strict standalone scoring still defaults to missing=error. Existing result paths are not overwritten.

## 公式与严格校验

对样本i，参考类别为G、预测类别为P：TP=|G∩P|，FP=|P−G|，FN=|G−P|。

- Precision = TP/(TP+FP)
- Recall = TP/(TP+FN)
- F1 = 2TP/(2TP+FP+FN)
- Subset accuracy：P=G的样本比例
- Jaccard：逐样本 |G∩P|/|G∪P| 后平均
- Hamming loss：(FP+FN)总数/(样本数×19)，Hamming accuracy=1−loss

micro先汇总计数再计算，macro对全19类等权，samples对逐样本指标等权；额外单列仅正例支持类macro。逐过程统计是该过程下各类计数的micro，不是压成单个过程标签。precision/recall/F1除零取0；真实发布集排除空参考集合。

同类多个O不重复计数。277条参考样本均需预测；漏答默认报错，仅显式 `--missing empty` 才按空集计分并列出ID。额外/排除ID、重复预测/类、非法类型和未知类一律报错。零样本报错，不输出伪造的0分。Hamming accuracy容易被大量真负例抬高，不宜作为主结论。

R15的实际U–Pb测年O05按维护方最终更正归M1；不归S1，不加入第20类。同一全局M1定义说明会给每个模型请求，绝不按当前样本动态提示答案。其他22条完整输入候选排除；不把整例全部操作当测年。

这是类别对齐诊断，不是10项实验命中4项的操作级必要实验召回，更不是对预算、顺序、采样与现场反馈的完整评价。维护方已于2026-10-27确认数据集获地学专家审核、无异议，当前按专家审核金标使用；见`data/expert_review_status.json`。保留的历史提取状态不代表仍待审；专家批准参考标签也不把原论文操作变成唯一或必要且充分的方案。

## English summary

The v2 scorer reports `acc`, `precision`, `recall`, `micro_f1`, and `macro_f1` for all 19 labels (`overall`) and each of the five label groups (`per_process`). `acc` is exact-set/subset accuracy, while precision and recall are micro-averaged. Group evaluation projects label columns but retains every case, including group-negative cases; it does not collapse a process into a single binary target. Empty/empty sets match for accuracy; undefined precision, recall and F1 are zero.

The v2 Harness preserves all 19 decisions and selected-class experiment designs, rationales and prospective evidence. Scoring still reads only task IDs and labels: changing narrative content cannot change a classification score. Protocol validation is not semantic evaluation; no LLM judge is currently invoked. The current expert-reviewed-gold status is based on maintainer confirmation and recorded separately from historical extraction metadata.

Macro-F1 is the unweighted mean of label-wise F1 over the fixed group, including unsupported labels. S4 currently has no positives, so a perfect oracle has overall fixed-label Macro-F1 18/19. The separately reported supported-label macro excludes labels without gold positives. `process_average` is an unweighted average across the five process scores, not a replacement for overall metrics. Scores use the [0, 1] scale. Legacy output keys remain available.
