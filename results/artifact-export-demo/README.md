# 指标导出示例：复算已有真实单样本输出

此目录只复算已保存的G19-0002预测，不调用模型、不重做实验规划，不是新的模型评测。
`gold.jsonl`为规范数据中该案例的标签投影，已核对与原始金标一致。
原始输入、真实模型回答及运行记录仍在[原样例目录](../../examples/model_runs/gpt61_sol_G19_0002_20261027/README.md)，没有改写。

在GitHub候选包根目录执行过：

```bash
python -m geotask19.metrics --data results/artifact-export-demo/gold.jsonl --predictions examples/model_runs/gpt61_sol_G19_0002_20261027/prediction.jsonl --output results/artifact-export-demo/metrics.json
```

该命令自动生成`metrics.json`及`metrics.json.artifacts/`中的指标JSON、CSV、错例、复算输入、来源与校验值。
评分器不会重建轨迹；此前原生子智能体没有提供可导出的完整工具事件列表，不能补造。
本地`results/`已加入gitignore，不随GitHub/Hugging Face候选包分发。
