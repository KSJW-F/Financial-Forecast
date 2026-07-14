# AI 金融报告分析市场走势

基于期货分析师日评/早评文章，自动提取品种趋势观点，入库并通过 Web 界面列表、详情与图表展示。

## 快速开始

> **Windows** 用 `copy`，**macOS/Linux** 用 `cp`。

### 0. 克隆仓库

```bash
git clone https://github.com/KSJW-F/Financial-Forecast.git
cd Financial-Forecast
```

### 1. 创建虚拟环境（推荐）

```bash
python -m venv .venv

# Windows PowerShell
.\.venv\Scripts\Activate.ps1

# macOS / Linux
source .venv/bin/activate
```

### 2. 安装依赖

```bash
pip install -r requirements.txt
```

### 3. 配置环境

```bash
# Windows
copy .env.example .env

# macOS / Linux
cp .env.example .env
```

默认使用 SQLite，无需额外安装数据库。如需 MySQL，修改 `.env` 中 `DATABASE_URL`。

### 4. 准备数据（必做，数据不在 Git 里）

向负责人获取 `data/` 压缩包，解压到项目根目录，详见 [data/README.md](data/README.md)。

### 5. 导入数据

```bash
# 导入前 500 条样本（推荐首次测试）
python scripts/import_data.py --limit 500

# 全量导入（约 7000+ 文件，耗时较长）
python scripts/import_data.py

# 清空后重新导入
python scripts/import_data.py --force --limit 500
```

### 6. 启动 Web 服务

```bash
python app.py
```

浏览器访问：http://127.0.0.1:5000

首页为**双门牌登录**。演示账号（启动时自动写入数据库）：

| 门户 | 账号 | 密码 | 用途 |
|------|------|------|------|
| 运营工作台 | `admin` | `admin123` | 研报分析、上传、决策参考、使用统计 |
| 客户市场端 | `guest` | `guest123` | 市场雷达（前5/前10/全部）、品种日热度、趋势图、AI 顾问 |

## 功能说明

- **企业端**：研报分析工作台、上传入库、决策参考、登录/访问心跳统计
- **客户端**：今日雷达（值得关注品种）、品种按日研报统计、点进原文、趋势图表、AI 顾问
- **文章详情**：清洗后正文与解析观点（登录后两端均可打开）

## 组员常见问题

| 问题 | 处理 |
|------|------|
| 列表为空 | 确认 `data/` 已解压并运行 `import_data.py` |
| `pip` 报错 | 使用 Python 3.10+，先激活 `.venv` |
| OCR 很慢 | 首次导入含图片/PDF OCR，可先 `--limit 500` |
| 想更新解析规则 | `python scripts/reextract_articles.py --unknown-only` |

## 项目结构

详见 [docs/设计文档.md](docs/设计文档.md)

## Web 功能

启动后访问 `http://127.0.0.1:5000`，登录后按角色分流：

| 路径 | 角色 | 说明 |
|------|------|------|
| `/` | 公开 | 双门牌登录 |
| `/enterprise` | 企业 | 工作台首页（数据 + 使用心跳） |
| `/reports` | 企业 | 研报分析列表 |
| `/upload` | 企业 | 上传 HTML/PDF/图片并入库 |
| `/insights` | 企业 | 决策参考 |
| `/market` | 客户 | 市场雷达（前5 / 前10 / 全部可滚动） |
| `/market/commodity` | 客户 | 品种洞察：按日热度 + 观点列表 |
| `/charts` | 登录 | 趋势图表 |
| `/advisor` | 登录 | AI 决策顾问 |
| `/article/<id>` | 登录 | 研报原文 |

上传 API：`POST /api/upload`（需企业登录；multipart：`file`，可选 `broker` / `publish_date`）  
顾问 API：`POST /api/advisor/chat`（需登录；JSON：`question`，可选 `commodity` / `date_from` / `date_to`）

## AI 分析（规则 + LLM 分层）

分析是**分层结合**的，不是二选一：

1. **强规则**：正文有「偏多 / 操作建议 / 【交易策略】」等明确句 → 直接采用（快、稳）
2. **LLM（文华/OpenAI）**：规则没有明确观点，或只有弱启发式时 → 调用 AI
3. **图表启发式**：AI 不可用时，用价表涨跌/数据跟踪做兜底
4. **未知**：正文空壳或完全无方向信息

在 `.env` 中配置（或直接复制 `.env.example`）：

```env
LLM_ENABLED=true
LLM_PROVIDER=wenhua
LLM_ONLY_UNKNOWN=true
WENHUA_AI_URL=https://swarm.wenhua.com.cn/aiservice/api/ShiXi/GetContent
```

对现有「趋势=未知」调用 AI 补全（**不要加 `--no-llm`**）：

```bash
# 先测试 20 条（会走 LLM）
python scripts/reprocess_chart_ai.py --limit 20

# 或仅重算预测、不重提取
python scripts/reprocess_predictions.py --use-llm --limit 20

# 图表型早报（五矿等）
python scripts/reprocess_chart_ai.py --chart-only --limit 20

# 全量未知（较慢）
python scripts/reprocess_chart_ai.py --limit 2000
```

说明：之前批量重跑常用 `--no-llm` 是为了在文华断线时也能用规则/启发式；文华可用时应去掉该参数，LLM 才会真正参与。

## 测试

```bash
pytest tests/ -v
```

## 文档

- [需求分析](docs/需求分析.md)
- [设计文档](docs/设计文档.md)
