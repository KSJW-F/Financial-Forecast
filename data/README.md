# 数据目录（不随 Git 上传）

本目录存放原始研报 HTML/PDF/PNG，体积较大，**不会提交到 GitHub**。

## 组员如何获取

1. 向项目负责人索取数据包（网盘 / 内网共享 / U 盘）
2. 解压到本目录，保持结构为：

```
data/
  20250401/
  20250402/
  ...
```

3. 在项目根目录执行导入：

```bash
python scripts/import_data.py --limit 500   # 首次可先试 500 条
python scripts/import_data.py               # 全量
```

4. 启动 Web：

```bash
python app.py
```

若无数据，Web 仍可启动，但列表为空。
