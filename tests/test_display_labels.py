from src.ui.display_labels import format_operation_object, format_source_label


def test_finance_setup_sources_are_human_readable():
    assert format_source_label("finance_setup_batch", 29) == (
        "财务启用·期初库存（批次 #29）"
    )
    assert format_source_label("finance_setup_quote", 18) == (
        "财务启用·期初应收（报价单 #18）"
    )
    assert format_source_label("finance_setup_supplier", 7) == (
        "财务启用·期初应付（供应商 #7）"
    )


def test_manual_source_hides_internal_uuid():
    assert format_source_label("manual", "63a5-internal-token") == "手工记账"


def test_operation_objects_are_human_readable():
    assert format_operation_object("quotes", 42) == "报价/出库单 #42"
    assert format_operation_object("payments", 9) == "收付款流水 #9"
    assert format_operation_object("customers", None) == "客户"


def test_unknown_values_remain_traceable():
    assert format_source_label("future_source", 3) == "其他业务来源（future_source #3）"
    assert format_operation_object("future_table", 3) == "其他业务对象 #3"
