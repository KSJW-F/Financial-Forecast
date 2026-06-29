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

## 功能说明

- **研报列表**：按期货公司、品种、趋势、日期筛选，支持分页与页码跳转
- **决策参考**：多机构共识、时序加权评分、买卖决策参考
- **趋势图表**：按品种查看趋势评分时间序列与分布
- **文章详情**：查看清洗后正文与解析观点、未知原因

## 组员常见问题

| 问题 | 处理 |
|------|------|
| 列表为空 | 确认 `data/` 已解压并运行 `import_data.py` |
| `pip` 报错 | 使用 Python 3.10+，先激活 `.venv` |
| OCR 很慢 | 首次导入含图片/PDF OCR，可先 `--limit 500` |
| 想更新解析规则 | `python scripts/reextract_articles.py --unknown-only` |

## 项目结构

详见 [docs/设计文档.md](docs/设计文档.md)

## AI 分析（文华接口）

项目已接入公司提供的文华 AI 接口，用于**规则无法识别**时的智能补全。

在 `.env` 中配置（或直接复制 `.env.example`）：

```env
LLM_ENABLED=true
LLM_PROVIDER=wenhua
WENHUA_AI_URL=https://swarm.wenhua.com.cn/aiservice/api/ShiXi/GetContent
```

对现有数据库中「趋势=未知」的文章调用 AI 补全（建议先小批量测试）：

```bash
# 先测试 20 条
python scripts/reprocess_predictions.py --use-llm --limit 20

# 全量补全未知文章（较慢，约数小时）
python scripts/reprocess_predictions.py --use-llm
```

新导入数据时，若 `LLM_ENABLED=true`，会自动在规则失败后调用 AI。

## 测试

```bash
pytest tests/ -v
```

## 文档

- [需求分析](docs/需求分析.md)
- [设计文档](docs/设计文档.md)
