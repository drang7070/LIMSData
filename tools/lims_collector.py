"""Read-only SQL Server acquisition. Run with Python 3.12 and tools/lims_deps."""
from __future__ import annotations
import argparse
import json
import os
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path
from contextlib import closing

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'tools/lims_deps'))
sys.path.insert(0, str(ROOT))
from lims import core as lims


def write_status(**payload):
    payload['updated_at'] = datetime.now().isoformat(timespec='seconds')
    temp = lims.STATUS.with_suffix('.tmp')
    temp.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    temp.replace(lims.STATUS)


def source_connection():
    import pyodbc
    cfg = lims.settings()
    def quote(v):
        return '{' + str(v).replace('}', '}}') + '}'
    cs = f'DRIVER={quote(cfg["driver"])};SERVER={quote(str(cfg["server"])+","+str(int(cfg["port"])))};DATABASE={quote(cfg["database"])};Encrypt=no;APP=MESDataProcess-LIMS-ReadOnly;'
    if cfg['auth'] == 'windows':
        cs += 'Trusted_Connection=yes;'
    else:
        credential = None
        if os.environ.get('LIMS_USERNAME') and os.environ.get('LIMS_PASSWORD'):
            credential = (os.environ['LIMS_USERNAME'], os.environ['LIMS_PASSWORD'])
        if os.name == 'nt' and not credential:
            from tools.windows_credentials import read_dpapi_credential, read_credential
            credential = read_credential('LIMSData/LIMS') or read_dpapi_credential(lims.DATA / '.lims_credentials.dpapi')
        if not credential:
            raise RuntimeError('尚未配置LIMS凭据，请运行 tools/lims_credentials.py')
        cs += f'UID={quote(credential[0])};PWD={quote(credential[1])};'
    conn = pyodbc.connect(cs, timeout=12, autocommit=True)
    conn.timeout = 180
    return conn


def _collect(days=140, date_basis='DATEENTER', start=None, end=None, chunk_days=7, fallback_sample_date=False):
    if not 1 <= days <= 3650 or date_basis not in ('DATEENTER', 'sampdate'):
        raise ValueError('采集参数无效')
    context = {'days': days, 'date_basis': date_basis, 'origin': 'live'}
    sql_date = 'COALESCE(DATEENTER,CAST(sampdate AS datetime))' if fallback_sample_date else f'[{date_basis}]'
    write_status(state='connecting', **context)
    try:
        with closing(source_connection()) as src, closing(lims.connect()) as db:
            plants = sorted({str(row[0]) for row in src.execute('SELECT DISTINCT PLANTID FROM finalresult_all') if row[0]})
            end = end or src.execute('SELECT GETDATE()').fetchone()[0]
            start = start or end.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(days=days)
            if start >= end:
                raise ValueError('采集开始时间必须早于结束时间')
            context.update(start=str(start), end=str(end), downloaded=0)
            db.execute('DROP TABLE IF EXISTS lims_staging')
            db.execute('CREATE TABLE lims_staging AS SELECT * FROM results WHERE 0')
            db.commit()
            cursor = start
            while cursor < end:
                stop = min(cursor + timedelta(days=chunk_days), end)
                write_status(state='fetching', window_start=str(cursor), window_end=str(stop), **context)
                # Identifiers are fixed/allowlisted; values are bound parameters. No device/sample exclusions.
                query = f'''SELECT FINAL,PLANTID,progname,ANALYTE,sampdate,SAMPLE_TYPE,UNITS,DATEENTER
                  FROM finalresult_all WHERE {sql_date} >= ? AND {sql_date} < ?'''
                reader = src.cursor()
                reader.execute(query, cursor, stop)
                columns = [d[0] for d in reader.description]
                while True:
                    batch = reader.fetchmany(5000)
                    if not batch:
                        break
                    records = [lims.record(dict(zip(columns, row)), 'live') for row in batch]
                    db.executemany(lims.INSERT.replace('INTO results(', 'INTO lims_staging('), records)
                    db.commit()
                    context['downloaded'] += len(records)
                    write_status(state='fetching', window_start=str(cursor), window_end=str(stop), **context)
                reader.close()
                cursor = stop
            fetched = db.execute('SELECT count(*) FROM lims_staging').fetchone()[0]
            source_count = src.execute(f'SELECT COUNT_BIG(*) FROM finalresult_all WHERE {sql_date} >= ? AND {sql_date} < ?', start, end).fetchone()[0]
            if fetched != context['downloaded'] or fetched != source_count:
                raise RuntimeError('源库与暂存记录数不符，未发布数据，请重试当前窗口')
            context['source_count'] = source_count
            local_date = 'entered_at' if date_basis == 'DATEENTER' else 'sampled_at'
            if fallback_sample_date:
                local_date = 'COALESCE(entered_at,sampled_at)'
            cols = 'origin,plant,sample,analyte,analyte_raw,sampled_at,entered_at,sample_type,unit,result_raw,value,qualifier,raw_json'
            # Atomic publish: a failed acquisition never replaces previously successful history.
            with db:
                db.execute(f"DELETE FROM results WHERE origin='live' AND {local_date} >= ? AND {local_date} < ?", (str(start), str(end)))
                db.execute(f'INSERT INTO results({cols}) SELECT {cols} FROM lims_staging')
                db.execute('DROP TABLE lims_staging')
                db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('last_live_sync', json.dumps(context)))
                db.execute('INSERT OR REPLACE INTO metadata VALUES (?,?)', ('plants', json.dumps(plants, ensure_ascii=False)))
            write_status(state='complete', **context)
            print(json.dumps({'success': True, **context}, ensure_ascii=False))
            return 0
    except Exception as exc:
        # ODBC errors can carry SQL/connection diagnostics: expose a category, never the connection string.
        error = str(exc)
        if 'handshake' in error or 'prelogin' in error or '10054' in error:
            message = '1433预登录握手被远端关闭，尚未完成认证。请检查公司网络和数据库连接。'
        elif '18456' in error or '28000' in error:
            message = 'SQL Server拒绝登录，请检查LIMS账号权限或密码。'
        elif '凭据' in error:
            message = '尚未配置LIMS凭据，请运行 tools/lims_credentials.py。'
        else:
            message = '采集未完成（' + type(exc).__name__ + '），原有本地数据保持可查询。请检查数据库和驱动。'
        write_status(state='failed', error=message, **context)
        print(json.dumps({'success': False, 'error': message}, ensure_ascii=False))
        return 1


def collect(days=140, date_basis='DATEENTER'):
    try:
        with lims.collection_guard():
            return _collect(days, date_basis)
    except ValueError as exc:
        print(json.dumps({'success': False, 'error': str(exc)}, ensure_ascii=False))
        return 1


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--days', type=int, default=140)
    parser.add_argument('--date-basis', choices=['DATEENTER', 'sampdate'], default='DATEENTER')
    args = parser.parse_args()
    sys.exit(collect(args.days, args.date_basis))
