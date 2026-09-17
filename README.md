<div align="center">

# 📦 货管家 · StockMate

**面向 IT 硬件经销商的一站式桌面管理工具**

![License](https://img.shields.io/badge/license-MIT-blue?style=flat-square)
![Python](https://img.shields.io/badge/python-3.10%2B-3776AB?style=flat-square&logo=python&logoColor=white)
![Platform](https://img.shields.io/badge/platform-Windows-0078D6?style=flat-square&logo=windows&logoColor=white)
![Version](https://img.shields.io/badge/version-v1.17-brightgreen?style=flat-square)
![GitHub](https://img.shields.io/badge/GitHub-CaoJun1015-181717?style=flat-square&logo=github&logoColor=white)

从上游价格表解析、报价、出库、收款到对账，一站式管理你的调货生意。

[功能介绍](#功能) · [快速开始](#快速开始) · [智能技能](#智能技能skills) · [项目结构](#项目结构) · [更新日志](#版本历史)

</div>

---

## 功能

| 模块 | 说明 |
|------|------|
| 📋 机型管理 | 从上游 Word 价格表自动导入，支持联想、拯救者、小新、ThinkPad 等系列 |
| 📦 库存批次 | 按批次管理进货，记录购入价、数量、上游供应商、SN 序列号 |
| 💰 报价操作 | 选机型 → 选批次 → 输入报价 → 一键复制文本或生成报价图片 |
| 🚚 出库管理 | 扫码 Enter 或多行粘贴 SN，唯一来源自动分配批次；服务层复核库存 |
| 👤 客户管理 | 客户联系方式 + 购买历史 + 欠款跟踪 |
| 🏭 上游管理 | 供应商联系方式 + 采购历史 |
| 💳 经营记账 | 资金账户、日常收支、应收应付、预收预付、退货退款、利润与账务审计 |
| 📊 客户对账单 | 按客户 + 日期范围生成对账单，导出 Excel |
| 📝 报价记录 | 全流程状态追踪：待确认 → 已报价 → 已出库 → 已收款 |
| 💾 数据安全 | 自动备份、库存与账务对账、SQLite 备份验证恢复、JSON 完整备份恢复与导出 |

### 智能技能（Skills）

内置 6 个专属 IT 经销商的智能技能，详见 [docs/skills.md](docs/skills.md)。

| Skill | 解决什么问题 |
|-------|-------------|
| 🧠 报价决策助手 | 基于历史报价数据自动推荐报价金额 |
| 🔔 价格异动哨兵 | 导入价格表时自动对比上一版本，检测新增/下架机型 |
| ⏰ 智能跟单提醒 | 扫描超期未成交订单，提醒跟进 |
| 📈 月度经营报告 | 自动生成销售额/毛利/TOP5/滞销预警/客户净收款分析 |
| 🔧 出库一条龙 | SN 批量扫描/校验/去重 + 出库确认单生成 |
| 🩺 远程诊断助手 | 结构化故障排查决策树（蓝屏/开不了机/WiFi/风扇） |

---

## 快速开始

第一次使用 v1.16 经营记账功能，请先阅读
[《经营记账零基础使用指南》](docs/v1.16_经营记账零基础使用指南.md)。

### 环境要求

- Python 3.10+
- Windows 10/11

### 安装与运行

```bash
# 克隆仓库
git clone https://github.com/CaoJun1015/diaohuo-assistant.git
cd diaohuo-assistant

# 安装依赖
pip install -r requirements.txt

# 启动
python run.py
```

### 打包为 exe

```bash
# 在 Python 3.11 的干净环境中安装固定构建清单
python -m pip install -r requirements-release.lock

# 完整回归、隔离 PyInstaller 打包、DLL 来源和 EXE 临时库启动检查
python scripts/release_validate.py
```

验证后的本地候选包和 JSON 报告位于 `release-candidates/`。报告会记录实际 EXE 名、提交、工作区状态、依赖版本、schema 和 SHA-256；脏工作区只标记为本地验证，不能作为正式发布候选。

---

## 项目结构

```
diaohuo-assistant/
├── run.py                  # 启动入口
├── build.spec              # PyInstaller 配置
├── requirements.txt        # 依赖清单
│
├── src/
│   ├── main.py             # PyQt6 主窗口组装
│   ├── models/
│   │   ├── schema.py       # schema v6
│   │   ├── migrations.py   # 事务化数据库迁移
│   │   ├── repositories.py # 业务数据写入
│   │   └── finance_queries.py # 财务只读查询
│   ├── services/           # 订单、库存、收付款、账本与退货事务
│   ├── ui/                 # 主业务页、FinanceTab 与对话框
│   └── utils/
│       ├── word_parser.py  # Word 价格表解析
│       ├── image_gen.py    # 报价图片生成
│       ├── excel_export.py # Excel 导出
│       ├── json_export.py  # JSON 导入导出
│       └── skills/         # 智能技能模块
│           ├── price_diff.py
│           ├── follow_up.py
│           ├── monthly_report.py
│           ├── shipment_flow.py
│           ├── quote_assist.py
│           └── remote_diagnose.py
│
├── data/                   # 数据目录（数据库不提交到 git）
├── docs/                   # 文档
└── skills/                 # TRAE SOLO Skill
    └── it-remote-diagnose/ # 远程诊断助手（可独立使用）
```

---

## 依赖

| 包 | 用途 |
|----|------|
| `PyQt6` | 桌面 GUI 框架 |
| `python-docx` | 读取 Word 价格表 |
| `Pillow` | 生成报价图片 |
| `openpyxl` | 导出 Excel 报表 |

---

## 版本历史

| 版本 | 日期 | 主要更新 |
|------|------|---------|
| v1.17 | 2026-08 | 不可变库存流水、跨批次出库分配、精确批次成本追溯 |
| v1.16 | 2026-08 | 经营记账闭环、不可变复式账本、预收预付、退货退款与利润分析 |
| v1.15 | 2026-08 | 移除旧数据库门面、金额统一整数分、账务审计追踪 |
| v1.14 | 2026-08 | UI/Service 数据边界收口、自动对账、可回滚 SQLite 备份恢复 |
| v1.13 | 2026-08 | 服务层、版本化迁移、整数分、软删除与不可变收付款流水 |
| v1.10 | 2026-07 | 自动备份、模糊搜索扩展、表格排序、操作日志、收款逾期提醒 |
| v1.09 | 2026-07 | 出库逻辑封装(ship_quote)、状态机守卫、状态回退优化、统计排除已取消报价 |
| v1.08 | 2026-07 | 修复出库SN保存/paid字段/库存回补/供应商欠款/批量收款事务等数据一致性问题 |
| v1.05 | 2026-05 | 智能技能系统（6 个 Skill）、条码枪支持、价格异动哨兵 |
| v1.04 | 2026-05 | 批量报价图片、群发功能 |
| v1.03 | 2026-05 | 自动备份、操作日志、客户购买历史、JSON 备份 |
| v1.02 | 2026-05 | 报价数量支持、实时库存管理 |
| v1.01 | 2026-05 | 备注列显示、Word 解析修复 |
| v1.0 | 2026-05 | 初始版本 |

完整更新日志见 [CHANGELOG.md](CHANGELOG.md)

---

<div align="center">

**如果这个项目对你有帮助，欢迎点个 ⭐ Star 支持一下！**

[MIT License](LICENSE) · [报告问题](https://github.com/CaoJun1015/diaohuo-assistant/issues)

</div>

当前架构收敛改动的业务口径、完整恢复步骤与验证命令见 [操作说明](docs/架构收敛与完整恢复说明.md)。
