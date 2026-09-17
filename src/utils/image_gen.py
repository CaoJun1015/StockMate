"""
图片生成模块：生成带水印的报价图片
"""

import os
from uuid import uuid4
from datetime import datetime
from PIL import Image, ImageDraw, ImageFont


WATERMARK_TEXT = "济南众诺伟业 18953180609"

# 尝试几个常见字体路径
_FONT_CANDIDATES = [
    "C:\\Windows\\Fonts\\msyh.ttc",       # 微软雅黑
    "C:\\Windows\\Fonts\\msyhbd.ttc",     # 微软雅黑粗体
    "C:\\Windows\\Fonts\\simhei.ttf",     # 黑体
    "C:\\Windows\\Fonts\\simsun.ttc",     # 宋体
]


def _get_font(size=24, bold=False):
    candidates = _FONT_CANDIDATES
    if bold:
        candidates = [
            "C:\\Windows\\Fonts\\msyhbd.ttc",
            "C:\\Windows\\Fonts\\simhei.ttf",
            *candidates,
        ]
    for font_path in candidates:
        if os.path.exists(font_path):
            try:
                return ImageFont.truetype(font_path, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate_quote_image(products, output_path=None, rows_per_page=20):
    """
    生成一张或多张报价图片。
    
    products: list of dict, 每项包含 series, cpu, ram, storage, gpu, screen, note
    output_path: 输出路径，默认生成到桌面 diaohuo_报价_{时间戳}.png
    
    返回: [output_path1, output_path2, ...]
    """
    if not output_path:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(desktop, f"diaohuo_报价_{timestamp}.png")

    # 参数配置
    margin = 40
    row_height = 36
    col_widths = [180, 120, 80, 80, 80, 60, 100]  # 系列, CPU, 内存, 硬盘, 显卡, 屏, 备注
    header_height = 44
    footer_height = 50
    col_count = len(col_widths)
    total_width = margin * 2 + sum(col_widths)

    # 计算行数
    total_rows = len(products)
    pages = []
    start = 0
    while start < total_rows:
        end = min(start + rows_per_page, total_rows)
        pages.append(products[start:end])
        start = end

    output_paths = []
    temporary_paths = []
    for page_idx, page_products in enumerate(pages):
        page_height = margin * 2 + header_height + len(page_products) * row_height + footer_height

        img = Image.new("RGB", (total_width, page_height), "white")
        draw = ImageDraw.Draw(img)

        title_font = _get_font(20, bold=True)
        header_font = _get_font(16, bold=True)
        cell_font = _get_font(15)
        footer_font = _get_font(14)

        # 标题
        draw.text((margin, 8), "货源报价单", fill="#333333", font=title_font)

        # 表头
        y = margin + 4
        headers = ["系列", "CPU", "内存", "硬盘", "显卡", "屏幕", "备注"]
        x = margin
        for i, h in enumerate(headers):
            draw.text((x + 6, y), h, fill="#333333", font=header_font)
            # 绘制列分隔线
            draw.line([(x, y - 2), (x, y + header_height + 2)], fill="#CCCCCC", width=1)
            x += col_widths[i]
        draw.line([(margin, y - 2), (margin + sum(col_widths), y - 2)], fill="#999999", width=1)
        draw.line([(margin, y + header_height + 2), (margin + sum(col_widths), y + header_height + 2)], fill="#999999", width=1)

        # 数据行
        y = y + header_height + 4
        for idx, prod in enumerate(page_products):
            x = margin
            row_data = [
                prod.get("series", ""),
                prod.get("cpu", ""),
                prod.get("ram", ""),
                prod.get("storage", ""),
                prod.get("gpu", ""),
                prod.get("screen", ""),
                prod.get("note", ""),
            ]
            bg_color = "#F5F5F5" if idx % 2 == 1 else "#FFFFFF"
            draw.rectangle([(margin, y), (margin + sum(col_widths), y + row_height)], fill=bg_color)

            for i, val in enumerate(row_data):
                draw.text((x + 6, y + 8), val, fill="#444444", font=cell_font)
                x += col_widths[i]

            y += row_height

        # 水印（底部）
        y_footer = margin + header_height + len(page_products) * row_height + 8
        draw.text((margin, y_footer), WATERMARK_TEXT, fill="#AAAAAA", font=footer_font)

        # 页码
        if len(pages) > 1:
            draw.text(
                (total_width - margin - 80, y_footer),
                f"{page_idx + 1}/{len(pages)}",
                fill="#AAAAAA",
                font=_get_font(12),
            )

        # 输出
        if len(pages) == 1:
            save_path = output_path
        else:
            base, ext = os.path.splitext(output_path)
            save_path = f"{base}_p{page_idx + 1}{ext}"

        os.makedirs(os.path.dirname(save_path) or ".", exist_ok=True)
        base, ext = os.path.splitext(save_path)
        temporary = f"{base}.{uuid4().hex}.tmp{ext}"
        try:
            img.save(temporary, "PNG")
        except Exception:
            for old in temporary_paths:
                if os.path.exists(old):
                    os.remove(old)
            raise
        output_paths.append(save_path)
        temporary_paths.append(temporary)

    try:
        for temporary, output in zip(temporary_paths, output_paths):
            os.replace(temporary, output)
    except Exception:
        for temporary in temporary_paths:
            if os.path.exists(temporary):
                os.remove(temporary)
        raise

    return output_paths


def generate_single_quote_card(series, cpu, ram, storage, gpu, screen, note, 
                                 customer_name="", quote_price="", output_path=None,
                                 quote_quantity=0, total_price=""):
    """生成单机报价卡片（用于私聊发图）"""
    if not output_path:
        desktop = os.path.join(os.path.expanduser("~"), "Desktop")
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        output_path = os.path.join(desktop, f"报价_{series}_{timestamp}.png")

    card_width = 500
    card_height = 280

    img = Image.new("RGB", (card_width, card_height), "white")
    draw = ImageDraw.Draw(img)

    title_font = _get_font(22, bold=True)
    info_font = _get_font(16)
    price_font = _get_font(28, bold=True)
    watermark_font = _get_font(12)

    y = 20
    draw.text((30, y), series, fill="#222222", font=title_font)

    y += 44
    parts = []
    if cpu:
        parts.append(f"CPU: {cpu}")
    if ram:
        parts.append(f"内存: {ram}")
    if storage:
        parts.append(f"硬盘: {storage}")
    if gpu:
        parts.append(f"显卡: {gpu}")
    if screen:
        parts.append(f"屏幕: {screen}")

    line = "  |  ".join(parts)
    draw.text((30, y), line, fill="#555555", font=info_font)

    if note:
        y += 32
        draw.text((30, y), f"备注: {note}", fill="#888888", font=_get_font(14))

    y += 42
    if quote_price:
        if quote_quantity > 1 and total_price:
            price_text = f"报价: {quote_price} × {quote_quantity} = {total_price}"
        else:
            price_text = f"报价: {quote_price}"
        draw.text((30, y), price_text, fill="#D32F2F", font=price_font)

    if customer_name:
        y += 50
        draw.text((30, y), f"客户: {customer_name}", fill="#666666", font=_get_font(14))

    draw.text((30, card_height - 30), WATERMARK_TEXT, fill="#CCCCCC", font=watermark_font)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    img.save(output_path, "PNG")
    return output_path
