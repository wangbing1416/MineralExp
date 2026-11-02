# Harness 接口及防泄露边界 / Harness contract

## 输入

协议为 **`geotask19.harness.v2`**。适配器从stdin读取单个UTF-8 JSON请求，向stdout返回单个UTF-8 JSON对象，不能用Markdown代码围栏包裹。模型、工具和内部Agent流程由适配器实现；必须将实际请求中的任务、完整标签空间、指令及输出要求传给受测模型，不能只传三项输入而遗漏分类标准。

`project_input(row)`新建白名单对象，只取中性`task_id`以及`hypothesis`、`initial_information`、`available_conditions`三项文本；其状态必须为`source_supported`且案例可评测。`make_request()`再添加：

- `protocol`：固定v2协议。
- `instruction`：所有案例相同的规划与输出要求，不含个案答案。
- `label_space`：完整19类的ID、过程、名称；taxonomy有通用`definition`时也提供，包括M1的沉积年龄边界。
- `response_schema`：实际JSON Schema，定义下述结构。

适配器可将Schema作为文本指令使用；**不要求每家供应商原生支持该Schema的全部关键字**。所有模型都应收到相同的任务协议，不能给不同模型隐藏地增加专家答案提示。

## 输出：19类决策、实验设计、理由与证据

| 字段 | 要求 | 当前是否评分 |
| --- | --- | --- |
| `labels` | 选中类别ID列表，不能重复或包含未知类 | **是，唯一分类评分来源** |
| `class_predictions` | 必须恰好19项，每类一次 | 仅校验结构及与`labels`的一致性 |
| 每项`class_id` | 现有19类中的ID | 不重复计分 |
| 每项`selected` | 布尔值true／false，不接受0／1或字符串 | 必须与`labels`一致 |
| 每项`rationale` | 非空文本，说明为什么选择或排除该类 | 不评科学质量 |
| 每项`experiments` | 选中类至少1项，未选中类必须为`[]` | 不评科学质量 |
| `plan` | 可选字符串，补充整体顺序、共享实验或综合方案 | 不评分 |

每项实验的七个字段均为必填非空字符串：

| 字段 | 说明 |
| --- | --- |
| `name` | 实验名称 |
| `design` | 具体设计：材料／样品、方法、步骤、比较／对照和质控 |
| `why_needed` | 为什么需要该实验，它与当前假说和条件是什么关系 |
| `expected_evidence` | 计划获取什么观测或测量证据，不得虚构已测结果 |
| `supports_hypothesis_if` | 什么条件性结果会支持假说或某种竞争解释 |
| `weakens_hypothesis_if` | 什么条件性结果会削弱、反驳或限制假说 |
| `limitations` | 假设、混杂因素、未确认的前提和不能得出的结论 |

如果某实验不能直接支持／反驳假说，应在相应字段解释，不可留空或冒充决定性证据。额外设备、预算、样品和资源需求应明确标为建议或未确认前提，不得当作输入已给定的事实。一个实验可服务多类，可在设计或整体`plan`说明共享关系；不是要求做19套无关实验。

下例仅展示两类的结构节选，**不能直接提交：实际必须补齐全部19类**。文本是字段占位说明，不是科学建议或参考答案：

```json
{
  "labels":["S1"],
  "class_predictions":[
    {
      "class_id":"S1",
      "selected":true,
      "rationale":"结合假说和已有信息，说明为什么选这一类。",
      "experiments":[{
        "name":"实验名称",
        "design":"明确样品、方法、步骤、比较与质控。",
        "why_needed":"说明该实验如何检验假说及为何需要。",
        "expected_evidence":"将获取的观测或测量，不是已知结果。",
        "supports_hypothesis_if":"说明支持假说的条件性结果。",
        "weakens_hypothesis_if":"说明削弱假说的条件性结果。",
        "limitations":"说明混杂、假设与不能作出的推断。"
      }]
    },
    {
      "class_id":"S2",
      "selected":false,
      "rationale":"说明在当前假说和条件下为什么不选。",
      "experiments":[]
    }
  ],
  "plan":"可选的整体顺序和共享实验说明。"
}
```

`examples/empty_harness.py`和`examples/structured_harness.py`能根据真实请求生成完整19项响应，分别用于空分类和正类设计字段的接口测试，**都不调用模型，不是科学基线或模型成绩**。

## 保存与错误处理

假设`--output results/predictions.jsonl`，除下列三个兼容文件外，还会自动创建`results/predictions.jsonl.artifacts/`并在调用结束后自动评分：

| 文件 | 内容 |
| --- | --- |
| `predictions.jsonl` | `geotask19.prediction.v2`：task_id、labels、完整逐类决策、实验七字段和可选plan；按固定类别顺序规范化，不改文本 |
| `predictions.jsonl.exchanges.jsonl` | 每次适配器调用的实际白名单请求、原始stdout响应、状态、退出码与耗时；也记录无效JSON、格式错误或超时的可用部分响应 |
| `predictions.jsonl.run.json` | `geotask19.run.v2`：模型／Harness身份、协议、数据／taxonomy哈希、完成状态及异常类型 |

结果目录支持`--artifacts-dir`覆盖，必须是新路径。包含`requests.jsonl`、`taxonomy.json`、持续更新的`run.json`、生命周期`events.jsonl`和逐样本索引`cases.jsonl`。按序号建立`samples/000001/`等目录，避免将不可信task_id用作路径。每个样本保存`request.json`、`raw_response.txt`、精确stdout字节`stdout.bin`、可解析时的`parsed_response.json`、通过校验时的`prediction.json`、`exchange.json`、`status.json`、`trajectory.json`和`usage.json`；供应商原始响应仅在适配器提供时另存`provider_response.json`。使用`manifest.json`核对文件大小和SHA-256。

成功运行自动保存`scores/metrics.json`、整体／逐过程／逐类／逐样本CSV、`errors.jsonl`、`gold_labels.jsonl`、完整`predictions.jsonl`和`provenance.json`；金标只由主进程在外部调用结束后用于评分，不通过stdin交给模型。模型请求仍不包含金标。金标和预测快照可用独立评分器复算。

响应必须满足Schema的结构约束及跨字段一致性；空理由、漏类别、重复类、未选中却列实验、选中却无实验、`labels`与`selected`不一致均报错，不静默修补。**格式合格不表示科学上正确，文本长短或质量不会增加分类分数。** 默认遇错停止；加`--continue-on-error`继续尝试剩余案例，但失败时退出码仍非零。失败／可捕获中断保存部分输出和状态，在`scores_partial/`明确按完整计划样本集、`missing=empty`计算诊断，记录缺失ID，不冒充成功完成。强制杀进程或磁盘故障不保证有最终分数，但保留此前已刷新的文件和状态。

新运行要求v2输出；旧的labels-only适配器需要升级。离线`geotask19.metrics`仍能读取历史labels-only预测，分类公式不变。若显式使用`--missing empty`，仍按既有规则将漏答计为空集并列出ID，不是对理由内容评分。

### 适配器可选轨迹封装 / Optional adapter trace envelope

默认仍可直接返回最终v2对象。若Harness提供多轮消息、工具调用、工具结果或用量，适配器可返回下面的外层对象；这不是要求模型改变其最终响应Schema：

```json
{
  "response": {"labels": [], "class_predictions": "此处须替换为完整19项合法决策"},
  "trajectory": [
    {"type": "message", "role": "assistant", "content": "公开的行动摘要"},
    {"type": "tool_call", "name": "tool-name", "arguments": {}},
    {"type": "tool_result", "name": "tool-name", "content": "实际返回结果"}
  ],
  "usage": {"input_tokens": 100, "output_tokens": 200},
  "provider_response": {"id": "可选的已脱敏供应商响应"}
}
```

上例为示意，不是可提交的最终决策；`response`必须完整通过v2校验。仅`response`必填；`trajectory`若提供必须为数组，`usage`必须为对象，`provider_response`可为任意合法JSON。不对供应商不同的事件内容强加统一语义，也不把它们计入分类指标。即使退出码非零，若stdout中有可解析的封装，仍保存可用轨迹。无效／截断JSON至少保留原始stdout字节，不臆造解析后的工具事件。

exchange仍是**任务请求和stdout响应记录**，只有适配器真正返回了工具事件，才有工具轨迹；运行器不能自动捕获未传回的Harness内部文件或恢复隐藏思维。`trajectory.json`的`provided_by_adapter`区分“未提供”与“明确返回空数组”；`usage.json`同理。`events.jsonl`是运行器生命周期日志，不是模型推理轨迹。脚本不请求／补造隐藏思维链。适配器如将轨迹写在其他文件中，需自行在外层封装中传回要保存的内容，或另外妥善保留原文件。

运行器不采集stderr、环境变量或HTTP鉴权头；适配器须清理所有返回内容中的鉴权信息和密钥。为忠实保存原始输出，脚本不保证自动识别／脱敏stdout中误打印的秘密。运行结果默认应视为私有，不要未经检查直接发布。已有结果目录不会被覆盖。

## 防泄露与安全

请求不含个案参考标签、参考实验O、金标理由、论文题名、DOI或原文证据指针。模板里的`expected_evidence`是要求模型**提出将获取的证据**，不是向模型泄露参考证据。全局定义不含样本ID或O编号，不按当前任务动态提示答案。

白名单及哨兵测试验证隐藏字段不改变请求。运行器不把数据路径传入子进程参数，但它不是OS沙箱；应在不挂载答案的受限环境运行能读文件或联网的Agent，防止检索本库或原论文答案。白名单也不证明输入语义绝无泄露。

## English summary

Protocol `geotask19.harness.v2` supplies the three allowlisted input texts, a neutral task ID, all 19 class IDs/names/processes, shared definitions, common instructions and a JSON Schema. Adapters must forward the full task contract to the evaluated model. Native provider-side structured-output support is optional; returning the documented JSON structure is required.

Responses contain `labels` and exactly 19 `class_predictions`, each with `class_id`, boolean `selected`, a nonempty `rationale`, and `experiments`. Selected classes require one or more experiments; unselected classes require an empty list. Each experiment requires nonempty `name`, `design`, `why_needed`, `expected_evidence`, `supports_hypothesis_if`, `weakens_hypothesis_if`, and `limitations`. An overall `plan` string is optional. Evidence is prospective and conditional, not fabricated observations; explain non-applicable interpretation criteria and unverified prerequisites explicitly.

Only `labels` enter classification scores. Structural validation is not scientific grading, and narrative changes cannot change scores. In addition to legacy sidecars, the runner automatically exports an `.artifacts/` directory with public requests, taxonomy, per-case raw stdout text/exact bytes, parsed/validated outputs, status, configuration/coverage checkpoints, lifecycle events, score JSON/CSV tables, errors, replayable label inputs and checksums.

An optional adapter envelope has required `response` (the unchanged final v2 object), optional `trajectory` (a list of actual returned events), `usage` (an object), and `provider_response` (sanitized JSON). Only the response labels are scored. Traces are saved as returned, including parseable nonzero-exit envelopes; absent traces are explicitly marked, never inferred or manufactured. This does not capture unreturned internal logs or hidden reasoning. Adapters must remove secrets from returned data; stderr, environment variables and auth headers are not automatically collected. Runtime artifacts should remain private until reviewed.

Failures remain fail-fast by default; `--continue-on-error` attempts all cases but exits nonzero on failure. Incomplete runs save `scores_partial/`, explicitly using the full scheduled split with missing=empty and recorded coverage, not a successful full-run result. Forced OS termination may leave checkpoints without final scores. Existing paths are not overwritten. Old labels-only adapters must upgrade, while offline scoring remains backward compatible.
