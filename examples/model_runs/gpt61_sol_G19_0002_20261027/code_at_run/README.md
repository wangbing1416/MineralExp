# Historical code / 运行时历史代码

These two files are byte-preserved snapshots of the runner and scorer recorded in
the original one-case `run.json` hashes, before the later artifact-export upgrade.
They are provenance records, not the active installed package. The original input,
response, prediction, metrics and run metadata have not been rewritten or rerun.
Current tests recompute the original scores with the current package as well.

这两份文件保留单样本实际运行时的代码及哈希，用于追溯；不是当前执行入口。
后续增加文件导出功能不应篡改旧运行的代码哈希，也不会重新生成模型答案。
