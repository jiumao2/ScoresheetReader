# ScoresheetReader

[English](README.md) | **简体中文**

ScoresheetReader 是一个本机运行的篮球记录表数字化工具。用户先从赛程中选择比赛、上传整张记录表照片，再按需调用 VLM 生成结构化草稿；识别结果直接进入可视化记录表编辑器，经过人工核对、确定性校验后导出 PDF。

当前识别方案不使用 OCR。正式调用使用 `qwen3.8-max` 和阿里云 OpenAI 兼容接口；公开测试和 CI 使用零 token 的确定性 Mock，不读取 `QWEN_API_KEY`。

## 当前功能

- 导入 `Schedule_2026北大杯.json`、`男篮.xlsx` 和 `女篮.xlsx`。赛程与报名表预处理到 SQLite；球员只保留内部 ID、所属球队和唯一姓名，报名号码被完全忽略。
- 比赛选择器会禁用球队未确定的占位比赛，并显示“待上传、已上传、已识别、已提交”状态；已有记录表的比赛可单击直接打开。创建草稿时固化比赛先验快照，并锁定竞赛名称、日期、时间、地点和 A/B 队名称；比赛序号保持可编辑空值。
- 整张图片一次识别：仅做 EXIF 方向规范化、约 6.3 MP 整图重采样和高质量 JPEG 4:4:4 编码，并设置 `vl_high_resolution_images=true`；不做 OCR、自动裁切或透视校正。
- 动态 Pydantic Schema 将球员姓名限制为本队唯一姓名或 `null`。模型输出不含置信度、候选姓名、内部 ID、别名或推理过程。
- 首次识别可自动载入编辑器；重新识别必须先查看按区域划分的新旧差异，再由用户选择合并区域，不会静默覆盖人工修改。
- 识别响应采用流式传输；右侧只显示安全的任务阶段、模型备注、空值/异常位置、确定性校验问题，以及 API 最后一包返回的实际 token 用量。原始思考文本不会保存或展示，异常高亮不会进入 SVG/PDF 导出。
- 三栏对照工作区支持可折叠侧栏、可拖动分隔线、原图与标准表拖动/滚轮缩放、照片重载和透明叠加。
- 所有纸面符号均通过语义控件输入：首发/替补、队员和教练员犯规、`Pc`、暂停、全队犯规、1/2/3 分、节末和比赛结束标记。
- 支持撤销/重做、750 ms 自动保存、显式“保存草稿”、刷新恢复、SQLite 修订历史、乐观并发冲突保护、确定性校验和“提交记录表”。保存失败或冲突时会停止校验和提交，避免提交服务器旧版本。
- SVG 预览与 ReportLab PDF 使用同一语义数据和模板坐标；PDF 通过 pypdf 与原始模板合并。

## 工作流与架构

```text
赛程/报名表 ──> SQLite 主数据 ──> 比赛先验快照
                                      │
照片 ──> Qwen 整图结构化输出 ──> ScoresheetDocument 草稿
                                      │
                        React + PDF.js + SVG 编辑器
                                      │
                   确定性校验 ──> 人工确认 ──> PDF
```

同一图片、先验、模型、Prompt、Schema 和预处理版本会命中本地缓存，不产生第二次模型调用。详细的数据边界和合并规则见[架构说明](docs/ARCHITECTURE.md)，FIBA 记号兼容性见[记号审计](docs/fiba-notation-audit.md)。

## 环境要求与安装

- Conda
- Node.js 与 npm
- 本机 `scoresheet_template.pdf`（有意不纳入版本控制）
- 用于 PDF 导出的 TrueType 中文字体；Windows 会自动检测微软雅黑

Python 必须运行在独立的 Python 3.11 Conda 环境中：

```powershell
conda create -n scoresheet-reader python=3.11
conda activate scoresheet-reader
python -m pip install -e ".\backend[dev]"
npm install
```

将模板放在仓库根目录，或者设置 `SCORESHEET_TEMPLATE_PATH`。主数据准备、预处理和保存位置见下一节。其他配置见 [.env.example](.env.example)；应用不会自动加载 `.env` 文件，必须在启动后端的同一个终端中设置环境变量。

## 数据准备、预处理与保存位置

### 1. 准备私有主数据

建议把私有主数据放在仓库以外的独立目录。目录中需要恰好准备以下三类文件：

```text
C:\private\scoresheet-master-data\
├── Schedule_2026北大杯.json
├── 男篮.xlsx
└── 女篮.xlsx
```

- `Schedule_*.json` 实际采用 JSONL 格式：每个非空行是一场比赛的 JSON 对象。程序会按文件名排序并只读取第一份匹配文件，因此同一目录建议只保留一份有效赛程。
- 每场比赛至少需要 `_id`、`group`、`home_team`、`away_team`、`time.$date` 和 `place`。`time.$date` 应是带时区的 ISO 8601 时间；程序会转换为 `Asia/Shanghai` 日期和时间。
- `男篮.xlsx` 和 `女篮.xlsx` 可包含 `男甲`、`男乙`、`女甲`、`女乙` 工作表。程序从第 2 行开始读取，A 列是球队名称，B 列是球员唯一姓名；其他列（包括报名号码）不会导入。
- 球员姓名只做 Unicode NFKC、首尾空白删除和连续空格合并。同一组别、同一球队内出现重复的规范化姓名会阻止整批导入。
- 赛程中的球队必须能连接到同组别报名表球队。未能连接的比赛仍会显示，但会标记为不可上传；修正源文件后重启后端即可重新预处理。

一个最小赛程行示例：

```json
{"_id":"game-001","group":"男甲","home_team":"示例学院甲","away_team":"示例学院乙","time":{"$date":"2026-03-21T10:00:00+08:00"},"place":"示例体育馆"}
```

在启动后端的 PowerShell 中指定目录和竞赛名称：

```powershell
$env:SCORESHEET_MASTER_DATA_DIR = "C:\private\scoresheet-master-data"
$env:SCORESHEET_COMPETITION_NAME = "2026北大杯"
```

若只需要公开演示或开发测试，可跳过私有 Excel/JSONL，改为：

```powershell
$env:SCORESHEET_MASTER_FIXTURE_PATH = "$PWD\shared\demo_master_data.json"
```

Fixture 格式见 [demo_master_data.json](shared/demo_master_data.json)。设置 `SCORESHEET_MASTER_FIXTURE_PATH` 后会优先使用该文件；不要同时依赖私有主数据目录。

### 2. 预处理何时发生

无需手工生成中间文件。每次启动 `scoresheet-reader` 时，后端会：

1. 读取并校验赛程与报名表，规范化球队和球员姓名；
2. 为球队和球员生成稳定的内部 ID，并连接赛程球队与报名球队；
3. 计算三份源文件的 SHA-256 来源摘要；
4. 来源摘要变化时，原子替换 SQLite 中派生的比赛、球队和球员表；摘要未变化时跳过重复导入。

预处理只替换派生主数据表，不删除已经上传的图片、记录表草稿、修订历史或识别结果。修改源文件后需要重启后端；可访问 `GET /api/v1/health` 检查 `master_data` 是否为 `ready`，导入错误会由比赛列表 API 明确返回。

创建记录表时，程序会把当时的比赛 ID、比赛信息、A/B 队标准名称和唯一姓名名单固化为 `GamePriorSnapshot`。因此之后重新导入报名表不会静默改变已有记录表；需要使用新主数据时应从比赛列表重新创建记录表。

### 3. 编辑器从哪里读取数据

浏览器不会直接访问 Excel、JSONL 或 SQLite：

- 比赛选择器通过 `GET /api/v1/games` 读取后端已经预处理的比赛列表，通过 `GET /api/v1/games/{id}` 获取比赛先验；
- 上传比赛记录表时，前端调用 `POST /api/v1/games/{id}/documents`，后端创建带先验快照的草稿；
- 编辑器通过 `GET/PATCH /api/v1/documents/{id}` 读取和自动保存文档，通过文档的 `source` URL 读取上传图片；
- 浏览器 `localStorage` 只保存最近打开的文档 ID、三栏比例等界面状态，以及未创建到服务器的合成预览草稿。真实比赛数据以服务器 SQLite 为准。

### 4. 保存结果在哪里

默认数据目录是仓库中的 `data/`；生产或长期使用时建议把它显式设到仓库外：

```powershell
$env:SCORESHEET_DATA_DIR = "D:\ScoresheetReaderData"
```

| 内容 | 默认位置或行为 |
| --- | --- |
| 预处理主数据、当前记录表 JSON、全部修订、识别原始结果、模型备注、缓存键和 token 用量 | `data/scoresheet_reader.sqlite3` |
| 上传原图、EXIF 方向规范化副本和可选的对齐图 | `data/uploads/`，文件名以文档 UUID 开头 |
| SVG 预览 | 由 `/api/v1/documents/{id}/render.svg` 即时生成，不自动保存到磁盘 |
| PDF 导出 | 由 `/api/v1/documents/{id}/render.pdf` 即时生成，保存到浏览器选择的下载位置 |
| 私有源赛程与报名表 | 始终留在 `SCORESHEET_MASTER_DATA_DIR`，程序不会改写 |

`data/`、私有源文件、模板和导出产物均被 Git 忽略。备份时应在停止后端后同时复制整个 `SCORESHEET_DATA_DIR`；只复制 SQLite 会遗漏原始照片，只复制 `uploads/` 会遗漏已编辑和已提交的结构化数据。

## 本地运行

在已激活的 Conda 环境中启动 API：

```powershell
scoresheet-reader
```

在第二个终端中运行：

```powershell
npm run dev
```

打开 `http://127.0.0.1:5173`。两个服务都只绑定 `127.0.0.1`。

只有用户点击“整图识别”时才需要 API Key，且 Key 只由后端进程读取：

```powershell
$env:QWEN_API_KEY = "your-key"
$env:QWEN_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"
$env:QWEN_MODEL = "qwen3.8-max"
$env:QWEN_REASONING_EFFORT = "xhigh"
```

实现中的系统提示词、用户提示词和动态 Schema 位于 [recognition.py](backend/scoresheet_reader/recognition.py)。

## 测试

```powershell
conda activate scoresheet-reader
python -m pytest backend\tests
python -m ruff check backend scripts\private_photo_check.py
npm test
npm run build
npm run test:e2e
```

默认测试强制使用 Mock，不读取 API Key，Qwen token 和费用均为 0。浏览器测试覆盖“选比赛 → 上传 → 识别 → 编辑 → 撤销/重做 → 自动保存 → 刷新恢复 → 重新识别差异合并”。

唯一的付费测试有双重显式门禁，不属于普通测试或 CI。它只发送一次请求且不重试：

```powershell
$env:RUN_QWEN_LIVE = "1"
python -m pytest backend\tests\test_qwen_live.py -s
```

当前验证结果见[测试报告](docs/TEST_REPORT.md)。

## 隐私与仓库策略

- `test/`、`private_test/`、`data/`、模板、生成的 PDF、截图、SQLite 数据库和环境文件均被忽略。
- API Key 不写入数据库、前端或日志；模型只接收整张图片、A/B 队名以及各自的唯一姓名枚举。
- 赛程比分、赛程工作人员、报名号码、内部 ID 和球队连接别名不会进入提示词。
- 记录台区域只识别去重后的人员姓名列表，不由模型分配记录员、助理记录员、计时员或 24 秒计时员职位；纸面岗位可在编辑器中人工填写。记录台人员、裁判员和签名均允许为空，不产生必填校验问题。
- 公开 CI 只使用 [demo_master_data.json](shared/demo_master_data.json) 的合成人名和 Mock 结果。
- 请勿提交真实记录表照片或人工真值文件。

## 规则与后续范围

默认规则档案为 FIBA 2024。FIBA 2026 记号已作为独立目录预留，但不会按比赛日期自动切换；未来扩展只替换规则档案、Schema 枚举、渲染和校验规则，不改变识别接口。

微信小程序、账号、云存储、多人协作，以及与 PKUBA 主系统的集成仍未实现。

## 许可证

本项目采用 GNU General Public License v3.0 或更高版本。详见 [LICENSE](LICENSE)。
