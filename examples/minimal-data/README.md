# 最小主数据示例

本目录包含可由 ScoresheetReader 直接预处理的合成主数据：

- `Schedule_2026北大杯.json`：JSONL 文件，每个非空行是一场比赛；
- `男篮.xlsx`：`男甲` 工作表含两支示例球队，`男乙` 仅保留表头；
- `女篮.xlsx`：`女甲` 工作表含两支示例球队，`女乙` 仅保留表头。

两份 Excel 的 A 列为球队名称，B 列为球员唯一姓名，从第 2 行开始读取。示例不包含球衣号码，因为 ScoresheetReader 不从报名表导入号码。

在仓库根目录启动后端前设置：

```powershell
$env:SCORESHEET_MASTER_DATA_DIR = "$PWD\examples\minimal-data"
$env:SCORESHEET_COMPETITION_NAME = "2026北大杯"
scoresheet-reader
```

记录表 PDF 模板位于仓库根目录的 `scoresheet_template.pdf`。
