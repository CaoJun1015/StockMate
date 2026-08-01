# Code Agent 实现指令 — main.py 架构拆分

> 将 `src/main.py`（约 3500 行）拆分为独立模块。纯重构，不改任何业务逻辑。
> 代码基线：当前 `src/main.py`。

---

## 拆分目标

| 当前 | 目标 |
|------|------|
| `src/main.py` 3500行 | `src/main.py` ~300行（MainWindow 组装） |
| — | `src/ui/style.py` |
| — | `src/ui/dialogs.py` |
| — | `src/ui/quote_panel.py` |
| — | `src/ui/product_tab.py` |
| — | `src/ui/record_tab.py` |
| — | `src/ui/customer_tab.py` |
| — | `src/ui/supplier_tab.py` |
| — | `src/ui/utils.py` |

---

## 前置要求

1. 先 git 备份：`git tag -a v1.10-backup-before-split -m "拆分前备份"`
2. 创建 `src/ui/` 目录（含空 `__init__.py`）
3. 每完成一个文件，验证 `python -c "from src.ui.xxx import *"` 不报错

---

## Step 1：提取 APP_STYLE → `src/ui/style.py`

**操作**：从 `src/main.py` 剪切 `APP_STYLE = """..."""` 整段（约 160 行），粘贴到新文件。

**新文件内容**：

```python
"""全局样式常量"""
APP_STYLE = """
...（原样粘贴，含所有 CSS 规则）...
"""
```

**`src/main.py` 修改**：删除 `APP_STYLE`，添加 `from src.ui.style import APP_STYLE`。

---

## Step 2：提取对话框 → `src/ui/dialogs.py`

**操作**：从 `src/main.py` 剪切以下 9 个类，粘贴到新文件。

| 类名 | 行号（约） | 依赖 |
|------|-----------|------|
| `ShipmentDialog` | 52-147 | `QDialog`, `database.ship_quote`, `utils.shipment_flow` |
| `PaymentDialog` | 152-219 | `QDialog`, `database` |
| `PaymentEditDialog` | 224-283 | `QDialog`, `database` |
| `StatementDialog` | 288-493 | `QDialog`, `database` |
| `ProductEditDialog` | 666-721 | `QDialog` |
| `BatchDialog` | 726-796 | `QDialog`, `database` |
| `CustomerDialog` | 801-842 | `QDialog` |
| `QuoteEditDialog` | 847-941 | `QDialog`, `database` |
| `OperationLogDialog` | 1280-1324 | `QDialog`, `database.get_operation_logs` |

**新文件结构**：

```python
"""对话框组件"""
from PyQt6.QtWidgets import (...)
from PyQt6.QtCore import Qt, QDate
from datetime import datetime

from src.models.database import (
    # 仅列出实际需要的函数
    get_connection, ship_quote, add_quote, update_quote, add_payment,
    get_payments, get_operation_logs, get_batches, get_batch_remaining,
    get_customer_statement, get_all_customers, search_customers,
    add_customer, get_all_suppliers, get_supplier_payable,
    get_quote_by_id, get_all_products, search_products,
)
from src.utils.shipment_flow import parse_sn_input, validate_sn_list, check_sn_duplicates


class ShipmentDialog(QDialog):
    ...（原样粘贴）...

class PaymentDialog(QDialog):
    ...（原样粘贴）...

# ... 其余类 ...
```

**`src/main.py` 修改**：删除 9 个类，添加：

```python
from src.ui.dialogs import (
    ShipmentDialog, PaymentDialog, PaymentEditDialog, StatementDialog,
    ProductEditDialog, BatchDialog, CustomerDialog, QuoteEditDialog,
    OperationLogDialog,
)
```

---

## Step 3：提取 QuotePanel → `src/ui/quote_panel.py`

**操作**：从 `src/main.py` 剪切 `QuotePanel` 类（约 330 行，行号 946-1275），粘贴到新文件。

**新文件结构**：

```python
"""报价面板（批次管理 + 快速报价）"""
from PyQt6.QtWidgets import (...)
from PyQt6.QtCore import Qt

from src.models.database import (
    get_batches, get_batch_remaining, add_batch, delete_batch,
    add_quote, add_customer, search_customers, get_all_customers,
    get_total_remaining,
)
from src.utils.word_parser import parse_word_pricelist, preview_parse
from src.utils.image_gen import generate_single_quote_card, generate_quote_image, WATERMARK_TEXT

# 导入对话框
from src.ui.dialogs import BatchDialog, CustomerDialog, ProductEditDialog


class QuotePanel(QWidget):
    ...（原样粘贴，所有方法不变）...
```

**关键点**：`QuotePanel` 内部引用了 `BatchDialog`、`CustomerDialog`、`ProductEditDialog`，现在从 `src.ui.dialogs` 导入。

**`src/main.py` 修改**：删除 `QuotePanel` 类，添加：

```python
from src.ui.quote_panel import QuotePanel
```

---

## Step 4：提取产品 Tab → `src/ui/product_tab.py`

**操作**：从 `MainWindow` 类中剪切以下方法，移到新类 `ProductTab(QWidget)` 中。

| 方法 | 行号（约） |
|------|-----------|
| `_build_product_tab` | — |
| `refresh_product_list` | 2270 |
| `on_search` | 2293 |
| `on_product_selected` | 2296 |
| `on_add_product` | 2311 |
| `on_edit_product` | 2322 |
| `on_delete_product` | 2347 |

**新类结构**：

```python
"""产品管理 Tab"""
from PyQt6.QtWidgets import (...)
from src.models.database import (
    add_product, update_product, delete_product, add_operation_log,
    search_products, get_all_products,
)
from src.ui.dialogs import ProductEditDialog


class ProductTab(QWidget):
    """产品管理 Tab，嵌入 MainWindow"""
    
    def __init__(self, main_window, parent=None):
        super().__init__(parent)
        self.main = main_window  # 引用 MainWindow，用于跨 Tab 刷新
        self.current_product_id = None
        self.search_edit = None
        self.product_table = None
        self._build_ui()
    
    def _build_ui(self):
        """构建产品管理 Tab 的 UI"""
        # 从 MainWindow._build_product_tab 复制，但 self.xxx 改为直接引用
        ...
    
    def refresh_product_list(self, keyword=None):
        ...
    
    def on_search(self, text):
        ...
    
    # ... 其余方法 ...
```

**关键点**：
- `self.main` 持有 MainWindow 引用，用于调用 `self.main.quote_panel.refresh()` 等跨 Tab 操作
- 原 `self.search_edit`、`self.product_table` 等控件现在属于 `ProductTab` 实例
- 原 `MainWindow._build_product_tab` 中的 `self.xxx` 引用也需要改为 `self.product_tab.xxx`

**`src/main.py` 修改**：删除上述方法，`_build_product_tab` 改为：

```python
def _build_product_tab(self):
    self.product_tab = ProductTab(self)
    return self.product_tab
```

---

## Step 5：提取报价记录 Tab → `src/ui/record_tab.py`

**操作**：从 `MainWindow` 剪切以下方法，移到新类 `RecordTab(QWidget)`。

| 方法 | 行号（约） |
|------|-----------|
| `_build_records_tab` | 2171 |
| `refresh_records` | 2662 |
| `on_edit_quote` | 2753 |
| `on_delete_quote` | 2780 |
| `on_confirm_quote` | 1525 |
| `on_ship_quote` | 1546 |
| `on_receive_payment` | 1583 |
| `on_cancel_quote` | 1616 |
| `on_import_word` | 2801 |
| `on_broadcast` | 3306 |
| `on_export_records_excel` | 3377 |
| `on_export_excel` | 3387 |
| `on_follow_up` | 2907 |
| `on_monthly_report` | 2929 |
| `on_shipment_flow` | 3154 |

**新类结构**：同上模式，`self.main` 引用 MainWindow。

**`src/main.py` 修改**：`_build_records_tab` 改为：

```python
def _build_records_tab(self):
    self.record_tab = RecordTab(self)
    return self.record_tab
```

---

## Step 6：提取客户 Tab → `src/ui/customer_tab.py`

| 方法 | 行号（约） |
|------|-----------|
| `_build_customer_tab` | 2116 |
| `refresh_customer_list` | 2366 |
| `on_add_customer` | 2379 |
| `on_customer_cell_clicked` | 2390 |
| `on_edit_customer_from_table` | 2425 |
| `on_delete_customer` | 2454 |

---

## Step 7：提取供应商 Tab → `src/ui/supplier_tab.py`

| 方法 | 行号（约） |
|------|-----------|
| `_build_supplier_tab` | 2485 |
| `refresh_supplier_list` | 2576 |
| `on_add_supplier` | 2589 |
| `on_supplier_cell_clicked` | 2539 |
| `on_edit_supplier_from_table` | 2601 |
| `on_delete_supplier` | 2629 |

---

## Step 8：提取工具函数 → `src/ui/utils.py`

**操作**：从 `src/main.py` 剪切以下内容，粘贴到新文件：

- `_validate_date()` 函数（行 1330-1341）
- `_global_excepthook()` 函数（行 3471-3489）

**`src/main.py` 修改**：添加 `from src.ui.utils import _validate_date, _global_excepthook`。

---

## Step 9：精简 MainWindow

**操作**：删除所有已移走的类和方法。最终 `MainWindow` 只保留：

| 保留内容 | 说明 |
|----------|------|
| `__init__` | 初始化窗口、创建 Tab、工具栏、菜单 |
| `_init_menu_bar` | 菜单栏 |
| `_build_*_tab`（6 个） | 改为创建 Tab 实例的单行方法 |
| 跨 Tab 的工具栏方法 | `on_export_json`, `on_import_json`, `on_remote_diagnose`, `on_price_diff`, `on_quote_assist`, `on_statement`, `on_show_logs`, `on_import_word`（如果在 record_tab 中） |
| `_build_finance_tab` | 财务管理（如果未拆） |
| `refresh_finance` | 财务刷新 |
| `refresh_payment_flow` | 付款流水 |

---

## Step 10：更新 `build.spec`

在 `hiddenimports` 中添加所有新模块：

```python
hiddenimports=[
    "PyQt6", "PyQt6.QtWidgets", "PyQt6.QtCore", "PyQt6.QtGui",
    "openpyxl", "docx", "PIL", "psutil",
    "src.ui", "src.ui.style", "src.ui.dialogs", "src.ui.quote_panel",
    "src.ui.product_tab", "src.ui.record_tab", "src.ui.customer_tab",
    "src.ui.supplier_tab", "src.ui.utils",
],
```

---

## 跨 Tab 通信规范

拆分后，Tab 之间通过 `self.main` 引用互相调用：

| 场景 | 调用方式 |
|------|----------|
| 新增机型后刷新报价面板 | `self.main.quote_panel.refresh()` |
| 出库后刷新库存 | `self.main.quote_panel.refresh()` |
| 删除客户后刷新报价记录 | `self.main.record_tab.refresh_records()` |
| 编辑供应商后刷新财务 | `self.main.refresh_finance()` |

---

## 禁止事项

1. ❌ 不修改任何业务逻辑代码（只改 import 和类结构）
2. ❌ 不修改 `src/models/database.py`
3. ❌ 不修改 `src/utils/` 下的任何文件
4. ❌ 不修改数据库表结构
5. ❌ 不打包
6. ❌ 不修改 `tests/` 下的测试文件

---

## 验证清单

- [ ] `python -c "from src.ui.style import APP_STYLE"` 成功
- [ ] `python -c "from src.ui.dialogs import ShipmentDialog, PaymentDialog"` 成功
- [ ] `python -c "from src.ui.quote_panel import QuotePanel"` 成功
- [ ] `python -c "from src.ui.product_tab import ProductTab"` 成功
- [ ] `python -c "from src.ui.record_tab import RecordTab"` 成功
- [ ] `python -c "from src.ui.customer_tab import CustomerTab"` 成功
- [ ] `python -c "from src.ui.supplier_tab import SupplierTab"` 成功
- [ ] `python -c "from src.ui.utils import _validate_date, _global_excepthook"` 成功
- [ ] `python -c "from src.main import main"` 成功（不崩溃）
- [ ] `python -m pytest tests/ -q` 全部通过
- [ ] `build.spec` hiddenimports 已更新