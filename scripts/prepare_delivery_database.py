"""One-time legacy conversion, separate from the end user's application."""
import argparse
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from src.models.migrations import migrate_database
from src.models.connection import connect
from src.services.database_service import initialize_database
from src.services.reconciliation_service import ReconciliationService

def prepare(source, target):
    if target.exists():
        raise FileExistsError(target)
    target.parent.mkdir(parents=True,exist_ok=True)
    original_hash=hashlib.sha256(source.read_bytes()).hexdigest()
    src=sqlite3.connect(source.resolve().as_uri()+'?mode=ro&immutable=1',uri=True)
    dst=sqlite3.connect(target)
    src.backup(dst)
    dst.close()
    c=connect(target)
    nulls=c.execute('SELECT id FROM customers WHERE balance_cents IS NULL').fetchall()
    with c:
        for row in nulls:
            c.execute('UPDATE customers SET balance_cents=CAST(ROUND(balance*100) AS INTEGER) WHERE id=?',(row[0],))
            c.execute("INSERT INTO audit_events(entity_type,entity_id,action,before_json,after_json,reason) VALUES ('customers',?,'backfill_missing_cents','{\"balance_cents\":null}','{\"balance_cents\":0}',?)",(row[0],'一次性旧库转换：根据旧元金额补全分字段'))
    c.close()
    migrate_database(target)
    initialize_database(target)
    dst=sqlite3.connect(target); dst.row_factory=sqlite3.Row
    # Every original business value except the derived customer cache and
    # retired REAL columns must survive exactly. This includes known anomalies.
    preservation={}
    for table in ('products','batches','customers','suppliers','quotes','payments','payment_allocations','operation_logs'):
        source_cols=[r[1] for r in src.execute(f'PRAGMA table_info({table})')]
        target_cols={r[1] for r in dst.execute(f'PRAGMA table_info({table})')}
        common=[x for x in source_cols if x in target_cols and not (table=='customers' and x=='balance_cents')]
        select=','.join('"'+x+'"' for x in common)
        before=src.execute(f'SELECT {select} FROM {table} ORDER BY id').fetchall()
        after=[tuple(r) for r in dst.execute(f'SELECT {select} FROM {table} ORDER BY id')]
        if before!=after:
            raise AssertionError(f'Original business values changed: {table}')
        preservation[table]={'records':len(before),'all_original_shared_fields_preserved':True}
    supplier=dict(dst.execute('SELECT id,name,balance_cents FROM suppliers WHERE id=78').fetchone())
    assert supplier['balance_cents']==-60000
    assert dst.execute('SELECT amount_cents FROM payments WHERE id=1273').fetchone()[0]==1260100
    assert dst.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
    assert not dst.execute('PRAGMA foreign_key_check').fetchall()
    balances=[dict(r) for r in dst.execute("SELECT entity_id,before_json,after_json FROM audit_events WHERE action='rebuild_business_balance'")]
    report=ReconciliationService(target).run()
    # No structural/ledger mismatch introduced by this conversion.
    fatal={'SQLITE_INTEGRITY','FOREIGN_KEY','CENTS_TYPE','QUOTE_ALLOCATION','PAYMENT_ALLOCATION','SUPPLIER_BALANCE','LEDGER_UNBALANCED','CUSTOMER_LEDGER','SUPPLIER_LEDGER','INVENTORY_LEDGER','SHIPMENT_SNAPSHOT'}
    assert not [x for x in report.issues if x.code in fatal],report.format_text()
    dst.execute('PRAGMA wal_checkpoint(TRUNCATE)')
    dst.execute('PRAGMA journal_mode=DELETE')
    dst.close();src.close()
    assert hashlib.sha256(source.read_bytes()).hexdigest()==original_hash
    result={'source_sha256':original_hash,'output_sha256':hashlib.sha256(target.read_bytes()).hexdigest(),'preservation':preservation,'null_customer_fields_filled':len(nulls),'supplier78':supplier,'customer_balance_cache_updates':balances,'reconciliation':report.format_text()}
    target.with_suffix('.conversion.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='customer_balance_cache_updates'},ensure_ascii=False,indent=2))

if __name__=='__main__':
    sys.stdout.reconfigure(encoding='utf-8')
    p=argparse.ArgumentParser();p.add_argument('source',type=Path);p.add_argument('target',type=Path)
    args=p.parse_args();prepare(args.source,args.target)
