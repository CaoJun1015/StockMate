"""
出库一条龙 (Shipment Flow)

优化出库流程：
- SN 批量扫描录入（支持条码枪连续扫入）
- SN 格式校验
- SN 去重检查
- 出库确认单文本生成
"""

import re
from datetime import datetime
from src.utils.money import format_yuan


def parse_sn_input(raw_text):
    """
    解析 SN 输入文本，支持多种分隔方式。

    参数:
        raw_text: str — 原始输入，支持换行、逗号、空格分隔

    返回:
        list[str] — 去重、去空值后的 SN 列表
    """
    if not raw_text or not raw_text.strip():
        return []

    # 支持多种分隔符：换行、中英文逗号、空格、制表符
    parts = re.split(r'[\n,，\s\t]+', raw_text.strip())
    # 去空值、去首尾空白
    sn_list = [s.strip() for s in parts if s.strip()]
    # 去重（保持顺序）
    seen = set()
    unique = []
    for sn in sn_list:
        if sn not in seen:
            seen.add(sn)
            unique.append(sn)
    return unique


def validate_sn(sn):
    """
    校验单个 SN 格式。

    常见 SN 格式：
    - 联想：通常 8-20 位字母数字组合，如 PF-XXXXXX 或 S/N:XXXXXX
    - 通用：至少 6 位，只含字母、数字、短横线

    返回:
        (bool, str) — (是否合法, 提示信息)
    """
    sn = sn.strip()
    if len(sn) < 6:
        return False, f"SN 过短（{len(sn)} 位），至少需要 6 位"
    if len(sn) > 30:
        return False, f"SN 过长（{len(sn)} 位），请检查是否多扫了"
    if not re.match(r'^[A-Za-z0-9\-\.]+$', sn):
        return False, f"SN 包含非法字符，只允许字母、数字、短横线"
    return True, "OK"


def validate_sn_list(sn_list, expected_count=None):
    """
    批量校验 SN 列表。

    参数:
        sn_list: list[str] — SN 列表
        expected_count: int — 期望数量（可选，与出库数量对比）

    返回:
        dict {
            valid: list[str],       # 合法的 SN
            invalid: list[tuple],   # (sn, error_msg)
            count_ok: bool,         # 数量是否匹配
            message: str,           # 汇总提示
        }
    """
    valid = []
    invalid = []

    for sn in sn_list:
        ok, msg = validate_sn(sn)
        if ok:
            valid.append(sn)
        else:
            invalid.append((sn, msg))

    count_ok = True
    if expected_count is not None and len(valid) != expected_count:
        count_ok = False

    # 构建提示
    parts = []
    if valid:
        parts.append(f"有效 SN: {len(valid)} 条")
    if invalid:
        parts.append(f"无效 SN: {len(invalid)} 条")
    if expected_count is not None:
        if count_ok:
            parts.append(f"数量匹配（需要 {expected_count} 台）")
        else:
            parts.append(f"⚠️ 数量不匹配！需要 {expected_count} 台，有效 SN {len(valid)} 条")

    return {
        "valid": valid,
        "invalid": invalid,
        "count_ok": count_ok,
        "message": " | ".join(parts),
    }


def check_sn_duplicates(sn_list, existing_sn_text=""):
    """
    检查新 SN 是否与已有 SN 重复。

    参数:
        sn_list: list[str] — 新录入的 SN 列表
        existing_sn_text: str — 已有的 SN 文本（逗号分隔）

    返回:
        dict {
            duplicates: list[str],  # 重复的 SN
            new_unique: list[str],  # 不重复的新 SN
            message: str,
        }
    """
    existing = set()
    if existing_sn_text:
        for s in re.split(r'[,，\s]+', existing_sn_text.strip()):
            if s.strip():
                existing.add(s.strip())

    duplicates = []
    new_unique = []
    for sn in sn_list:
        if sn in existing:
            duplicates.append(sn)
        else:
            new_unique.append(sn)

    msg_parts = []
    if duplicates:
        msg_parts.append(f"⚠️ {len(duplicates)} 条 SN 已存在: {', '.join(duplicates[:3])}")
    if new_unique:
        msg_parts.append(f"新增 {len(new_unique)} 条 SN")

    return {
        "duplicates": duplicates,
        "new_unique": new_unique,
        "message": " | ".join(msg_parts) if msg_parts else "无新 SN",
    }


def generate_shipment_receipt(quote, sn_list, batch_info=None):
    """
    生成出库确认单文本。

    参数:
        quote: dict — 报价记录（含 series, cpu, customer_name, quote_price, quote_quantity）
        sn_list: list[str] — 出库 SN 列表
        batch_info: dict — 批次信息（可选）

    返回:
        str — 格式化的确认单文本
    """
    now = datetime.now().strftime("%Y-%m-%d %H:%M")
    parts = []

    parts.append("=" * 36)
    parts.append("        出 库 确 认 单")
    parts.append("=" * 36)
    parts.append(f"日期: {now}")
    parts.append(f"客户: {quote.get('customer_name', '________')}")
    parts.append("")

    # 机型信息
    spec_parts = [quote.get("series", "")]
    for field in ["cpu", "ram", "storage", "gpu"]:
        val = quote.get(field, "")
        if val:
            spec_parts.append(val)
    parts.append(f"机型: {' '.join(spec_parts)}")

    price = quote.get("quote_price_cents", 0) or 0
    qty = quote.get("quote_quantity", 1) or 1
    parts.append(f"单价: {format_yuan(price)}")
    parts.append(f"数量: {qty} 台")
    if qty > 1:
        parts.append(f"合计: {format_yuan(price * qty)}")
    parts.append("")

    # SN 列表
    if sn_list:
        parts.append("SN 序列号:")
        parts.append("-" * 36)
        for i, sn in enumerate(sn_list, 1):
            parts.append(f"  {i:02d}. {sn}  [  ]")
        parts.append("-" * 36)
        parts.append("[  ] 外观完好  [  ] 开机正常  [  ] 配件齐全")
    parts.append("")

    parts.append("")
    parts.append("客户签字: ________________")
    parts.append("日期: ________________")
    parts.append("")
    parts.append("=" * 36)

    return "\n".join(parts)
