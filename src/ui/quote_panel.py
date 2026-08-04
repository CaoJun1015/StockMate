"""报价面板（批次管理 + 快速报价）"""
from datetime import datetime

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout,
    QTableWidget, QTableWidgetItem, QPushButton, QLabel,
    QComboBox, QLineEdit, QSpinBox, QGroupBox,
    QMessageBox, QApplication, QAbstractItemView,
    QCheckBox,
)
from PyQt6.QtCore import Qt

from src.models.queries import (
    get_batch_detail,
    get_batch_remaining,
    get_customer_default_tax_rate,
    list_batches,
    list_customers,
    list_suppliers,
)
from src.utils.money import format_yuan, yuan_to_cents
from src.services.exceptions import ServiceError
from src.services.inventory_service import InventoryService
from src.services.order_service import OrderService
from src.services.party_service import CustomerService
from src.utils.word_parser import parse_word_pricelist, preview_parse
from src.utils.image_gen import generate_single_quote_card, generate_quote_image, WATERMARK_TEXT

from src.ui.dialogs import BatchDialog, CustomerDialog, ProductEditDialog, _parse_tax_rate


class QuotePanel(QWidget):
    """报价操作面板，显示选中机型的批次详情并进行报价"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_product_id = None
        self.customer_service = CustomerService()
        self.inventory_service = InventoryService()
        self.order_service = OrderService()
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 8, 8, 8)

        # 机型信息
        self.product_label = QLabel("未选择机型")
        self.product_label.setObjectName("sectionTitleBlue")
        layout.addWidget(self.product_label)

        # 批次表格
        self.batch_table = QTableWidget()
        self.batch_table.setAlternatingRowColors(True)
        self.batch_table.setColumnCount(7)
        self.batch_table.setHorizontalHeaderLabels(["购入价", "进货数量", "剩余", "入库日期", "上游", "备注", "序列号"])
        self.batch_table.horizontalHeader().setStretchLastSection(True)
        self.batch_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.batch_table.setMinimumHeight(120)
        layout.addWidget(QLabel("库存批次:"))
        layout.addWidget(self.batch_table)

        # 批次操作按钮行
        btn_row1 = QHBoxLayout()
        self.add_batch_btn = QPushButton("+ 新增批次")
        self.add_batch_btn.setObjectName("primaryBtn")
        self.add_batch_btn.clicked.connect(self.on_add_batch)
        self.del_batch_btn = QPushButton("删除批次")
        self.del_batch_btn.setObjectName("dangerBtn")
        self.del_batch_btn.clicked.connect(self.on_delete_batch)
        self.refresh_batch_btn = QPushButton("刷新")
        self.refresh_batch_btn.setObjectName("ghostBtn")
        self.refresh_batch_btn.clicked.connect(self.refresh)
        btn_row1.addWidget(self.add_batch_btn)
        btn_row1.addWidget(self.del_batch_btn)
        btn_row1.addWidget(self.refresh_batch_btn)
        btn_row1.addStretch()
        layout.addLayout(btn_row1)

        # 报价操作区
        layout.addSpacing(10)
        group = QGroupBox("报价操作")
        glayout = QGridLayout(group)

        self.quote_price_spin = QSpinBox()
        self.quote_price_spin.setRange(0, 999999)
        self.quote_price_spin.setPrefix("¥ ")
        self.quote_price_spin.setValue(0)

        self.quote_quantity_spin = QSpinBox()
        self.quote_quantity_spin.setRange(1, 9999)
        self.quote_quantity_spin.setValue(1)

        self.customer_combo = QComboBox()
        self.customer_combo.setEditable(True)
        self.customer_combo.setPlaceholderText("输入客户名称或选择...")
        self.customer_combo.setMinimumWidth(150)
        self.customer_combo.currentIndexChanged.connect(self._on_customer_changed)

        self.quote_remark_edit = QLineEdit()
        self.quote_remark_edit.setPlaceholderText("备注（选填）")

        self.quote_paid_combo = QComboBox()
        self.quote_paid_combo.addItems(["否", "是"])

        glayout.addWidget(QLabel("对外报价:"), 0, 0)
        glayout.addWidget(self.quote_price_spin, 0, 1)
        glayout.addWidget(QLabel("报价数量:"), 0, 2)
        glayout.addWidget(self.quote_quantity_spin, 0, 3)
        glayout.addWidget(QLabel("客户:"), 1, 0)
        glayout.addWidget(self.customer_combo, 1, 1)
        glayout.addWidget(QLabel("是否打款:"), 1, 2)
        glayout.addWidget(self.quote_paid_combo, 1, 3)
        glayout.addWidget(QLabel("备注:"), 2, 0)
        glayout.addWidget(self.quote_remark_edit, 2, 1, 1, 3)

        btn_row2 = QHBoxLayout()
        self.quote_copy_btn = QPushButton("报价并复制文本")
        self.quote_copy_btn.setObjectName("primaryBtn")
        self.quote_copy_btn.clicked.connect(self.on_quote_and_copy)
        self.quote_img_btn = QPushButton("生成报价图片")
        self.quote_img_btn.setObjectName("ghostBtn")
        self.quote_img_btn.clicked.connect(self.on_quote_image)
        btn_row2.addWidget(self.quote_copy_btn)
        btn_row2.addWidget(self.quote_img_btn)
        btn_row2.addStretch()
        glayout.addLayout(btn_row2, 3, 0, 1, 4)

        layout.addWidget(group)

        # 价税覆盖层（独立行，右对齐）
        tax_row = QHBoxLayout()
        tax_row.addWidget(QLabel("税率:"))
        self.tax_combo = QComboBox()
        self.tax_combo.setEditable(True)
        self.tax_combo.addItem("无", None)
        self.tax_combo.addItem("8%", 0.08)
        self.tax_combo.addItem("13%", 0.13)
        self.tax_combo.setCurrentIndex(0)
        tax_row.addWidget(self.tax_combo)
        self.purchase_tax_check = QCheckBox("进价含税")
        tax_row.addWidget(self.purchase_tax_check)
        self.quote_tax_check = QCheckBox("售价含税")
        tax_row.addWidget(self.quote_tax_check)
        tax_row.addStretch()
        layout.addLayout(tax_row)

        layout.addStretch()

    def load_product(self, product_id, series, cpu, ram, storage, gpu, screen, note):
        self.current_product_id = product_id
        self.product_specs = {
            "series": series, "cpu": cpu, "ram": ram,
            "storage": storage, "gpu": gpu, "screen": screen, "note": note,
        }
        parts = [series]
        if cpu: parts.append(cpu)
        if ram: parts.append(ram)
        if storage: parts.append(storage)
        if gpu: parts.append(gpu)
        self.product_label.setText("  ".join(parts))
        self.refresh()

        # Skill 1: 加载报价建议
        try:
            from src.utils.quote_assist import suggest_price
            suggestion = suggest_price(series=series, cpu=cpu, ram=ram, storage=storage, gpu=gpu)
            if suggestion and suggestion.get("suggested_mid", 0) > 0:
                self.quote_price_spin.setValue(suggestion["suggested_mid"])
        except Exception:
            pass

    def refresh(self):
        if not self.current_product_id:
            return
        
        batches = list_batches(self.current_product_id)
        
        suppliers = {s["id"]: s["name"] for s in list_suppliers()}
        
        self.batch_table.setRowCount(len(batches))
        for i, b in enumerate(batches):
            price_item = QTableWidgetItem(format_yuan(b["purchase_price_cents"]))
            price_item.setData(Qt.ItemDataRole.UserRole, b["id"])
            self.batch_table.setItem(i, 0, price_item)
            self.batch_table.setItem(i, 1, QTableWidgetItem(str(b['quantity'])))
            self.batch_table.setItem(i, 2, QTableWidgetItem(str(b['remaining'])))
            self.batch_table.setItem(i, 3, QTableWidgetItem(b['date']))
            supplier_name = suppliers.get(b.get('supplier_id'), '')
            self.batch_table.setItem(i, 4, QTableWidgetItem(supplier_name))
            self.batch_table.setItem(i, 5, QTableWidgetItem(b.get('remark', '')))
            self.batch_table.setItem(i, 6, QTableWidgetItem(b.get('sn_list', '')))
        self.batch_table.resizeColumnsToContents()

        self.customer_combo.clear()
        customers = list_customers()
        for c in customers:
            self.customer_combo.addItem(c["name"], c["id"])

    def _on_customer_changed(self):
        """客户切换时自动填充默认税率"""
        customer_id = self.customer_combo.currentData()
        if customer_id:
            default_tax = get_customer_default_tax_rate(customer_id)
            if default_tax is not None:
                idx = self.tax_combo.findData(default_tax)
                if idx >= 0:
                    self.tax_combo.setCurrentIndex(idx)
                else:
                    self.tax_combo.setEditText(f"{int(default_tax * 100)}%")

    def on_add_batch(self):
        if not self.current_product_id:
            QMessageBox.warning(self, "提示", "请先选择机型")
            return
        dlg = BatchDialog(self)
        if dlg.exec():
            data = dlg.get_data()
            try:
                self.inventory_service.receive_batch(
                    product_id=self.current_product_id,
                    purchase_price_cents=yuan_to_cents(data["price"]),
                    quantity=data["quantity"],
                    date=data["date"],
                    remark=data["remark"],
                    supplier_id=data["supplier_id"],
                    sn_list=data["sn_list"],
                )
            except ServiceError as exc:
                QMessageBox.warning(self, "入库失败", str(exc))
                return
            self.refresh()

    def on_delete_batch(self):
        row = self.batch_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请先选择要删除的批次")
            return
        price_item = self.batch_table.item(row, 0)
        if not price_item:
            return
        batch_id = price_item.data(Qt.ItemDataRole.UserRole)
        if batch_id is None:
            return
        reply = QMessageBox.question(
            self, "确认删除", "确定删除此批次？关联的草稿报价也会从工作列表隐藏。",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                self.inventory_service.delete_batch(batch_id)
            except ServiceError as exc:
                QMessageBox.warning(self, "删除失败", str(exc))
                return
            self.refresh()

    def on_quote_and_copy(self):
        """报价并复制文本到剪贴板"""
        if not self._validate_quote():
            return
        product, batch, quote_price, quote_quantity, customer_name, remark = self._prepare_quote_data()
        success, message = self._record_quote(batch["id"], quote_price, quote_quantity, remark)
        
        if not success:
            QMessageBox.warning(self, "报价失败", message)
            return
        
        text = self._format_quote_text(product, quote_price, customer_name, quote_quantity)
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        QMessageBox.information(self, "成功", f"{message}\n报价已复制到剪贴板！")
        self.refresh()

    def on_quote_image(self):
        """生成报价图片"""
        if not self._validate_quote():
            return
        product, batch, quote_price, quote_quantity, customer_name, remark = self._prepare_quote_data()
        success, message = self._record_quote(batch["id"], quote_price, quote_quantity, remark)
        
        if not success:
            QMessageBox.warning(self, "报价失败", message)
            return
        
        total = quote_price * quote_quantity
        output = generate_single_quote_card(
            series=product.get("series", ""),
            cpu=product.get("cpu", ""),
            ram=product.get("ram", ""),
            storage=product.get("storage", ""),
            gpu=product.get("gpu", ""),
            screen=product.get("screen", ""),
            note=product.get("note", ""),
            customer_name=customer_name,
            quote_price=f"¥ {quote_price:.0f}",
            quote_quantity=quote_quantity,
            total_price=f"¥ {total:.0f}",
        )
        QMessageBox.information(self, "成功", f"{message}\n报价图片已生成:\n{output}")
        self.refresh()

    def _validate_quote(self):
        if not self.current_product_id:
            QMessageBox.warning(self, "提示", "请先在左侧选择机型")
            return False
        if self.quote_price_spin.value() <= 0:
            QMessageBox.warning(self, "提示", "请输入对外报价")
            return False
        row = self.batch_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "提示", "请选择一个库存批次")
            return False
        
        # 从表格行的 UserRole 中获取 batch_id
        price_item = self.batch_table.item(row, 0)
        if not price_item:
            return False
        batch_id = price_item.data(Qt.ItemDataRole.UserRole)
        if batch_id is None:
            return False
        
        quote_quantity = self.quote_quantity_spin.value()
        remaining = get_batch_remaining(batch_id)
        
        if remaining < quote_quantity:
            QMessageBox.warning(
                self, "库存不足", 
                f"库存不足！\n\n当前批次剩余: {remaining} 台\n本次报价数量: {quote_quantity} 台"
            )
            return False
        
        return True

    def _prepare_quote_data(self):
        row = self.batch_table.currentRow()
        if row < 0:
            return None, None, None, None, None, None
        # 从表格行的 UserRole 中获取 batch_id，避免重新查询导致索引不一致
        price_item = self.batch_table.item(row, 0)
        if not price_item:
            return None, None, None, None, None, None
        batch_id = price_item.data(Qt.ItemDataRole.UserRole)
        if batch_id is None:
            return None, None, None, None, None, None
        batch = get_batch_detail(batch_id)
        if not batch:
            return None, None, None, None, None, None
        product = getattr(self, 'product_specs', {})
        quote_price = self.quote_price_spin.value()
        quote_quantity = self.quote_quantity_spin.value()
        customer_name = self.customer_combo.currentText().strip()
        remark = self.quote_remark_edit.text().strip()
        return product, batch, quote_price, quote_quantity, customer_name, remark

    def _record_quote(self, batch_id, quote_price, quote_quantity, remark):
        customer_name = self.customer_combo.currentText().strip()
        customer_id = None
        if customer_name:
            customers = list_customers(customer_name)
            matched = [c for c in customers if c["name"] == customer_name]
            if matched:
                customer_id = matched[0]["id"]
            else:
                customer_id = self.customer_service.create(name=customer_name)

        today = datetime.now().strftime("%Y-%m-%d")
        tax_rate = _parse_tax_rate(self.tax_combo)
        try:
            self.order_service.create_quote(
                batch_id=batch_id,
                customer_id=customer_id,
                quote_price_cents=yuan_to_cents(quote_price),
                quote_quantity=quote_quantity,
                quote_date=today,
                remark=remark,
                tax_rate=tax_rate,
                purchase_tax_inclusive=self.purchase_tax_check.isChecked(),
                quote_tax_inclusive=self.quote_tax_check.isChecked(),
            )
            return True, "报价已保存（待确认）"
        except ServiceError as exc:
            return False, str(exc)

    def _format_quote_text(self, product, quote_price, customer_name, quote_quantity=1):
        parts = [product.get("series", "")]
        if product.get("cpu"): parts.append(product["cpu"])
        if product.get("ram"): parts.append(product["ram"])
        if product.get("storage"): parts.append(product["storage"])
        if product.get("gpu"): parts.append(product["gpu"])
        if product.get("screen"): parts.append(product["screen"])
        if product.get("note"): parts.append(product["note"])

        spec = " ".join(parts)
        if quote_quantity > 1:
            total = quote_price * quote_quantity
            price_text = f"报价: ¥{quote_price:.0f} × {quote_quantity} = ¥{total:.0f}"
        else:
            price_text = f"报价: ¥{quote_price:.0f}"
        text = f"{spec}\n{price_text}"
        if customer_name:
            text = f"{customer_name}\n{text}"
        text += f"\n{WATERMARK_TEXT}"
        return text
