"""Local LIMS history, independent of the MES production baseline databases."""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import subprocess
import sys
import threading
from contextlib import closing, contextmanager
from datetime import datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = Path(os.environ.get('LIMS_DATA_DIR', ROOT / 'data'))
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / 'lims_history.sqlite3'
CONFIG = ROOT / 'config/lims.json'
STATUS = DATA / 'lims_sync_status.json'
LOCK = threading.Lock()
PROCESS = None
FIELDS = {'plant': 'plant', 'sample': 'sample', 'analyte': 'analyte', 'sample_type': 'sample_type', 'unit': 'unit'}


@contextmanager
def collection_guard():
    """OS process lock shared by web refresh and historical backfill."""
    with open(DATA / 'lims_collection.lock', 'a+b') as handle:
        handle.seek(0)
        if os.fstat(handle.fileno()).st_size == 0:
            handle.write(b'0')
            handle.flush()
        handle.seek(0)
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            raise ValueError('已有LIMS采集正在运行，请等待完成') from None
        try:
            yield
        finally:
            handle.seek(0)
            if os.name == 'nt':
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def connect(path=DB):
    db = sqlite3.connect(path, timeout=30)
    db.row_factory = sqlite3.Row
    db.execute('PRAGMA journal_mode=WAL')
    db.executescript('''
      CREATE TABLE IF NOT EXISTS results (
        id INTEGER PRIMARY KEY, origin TEXT NOT NULL, plant TEXT, sample TEXT,
        analyte TEXT, analyte_raw TEXT, sampled_at TEXT, entered_at TEXT,
        sample_type TEXT, unit TEXT, result_raw TEXT, value REAL, qualifier TEXT,
        raw_json TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS lims_slice ON results(origin,plant,sample,analyte,sampled_at);
      CREATE INDEX IF NOT EXISTS lims_dates ON results(origin,sampled_at);
      CREATE TABLE IF NOT EXISTS metadata (key TEXT PRIMARY KEY, value TEXT);
    ''')
    return db


def settings():
    cfg = json.loads(CONFIG.read_text(encoding='utf-8'))
    for key in ('server','port','database','driver','auth'):
        cfg[key] = os.environ.get('LIMS_'+key.upper(), cfg[key])
    return cfg


def numeric(raw):
    if raw is None or not str(raw).strip():
        return None, '空值'
    text = str(raw).strip()
    if text in ('无效', '不成线'):
        return None, text
    text = text.replace('痕迹', '0.02').replace('少量', '0.08').replace('.0.', '0.')
    text = text.split(',')[0].strip()
    text = {'无': '0', '未检出': '0', '2.14.': '2.14'}.get(text, text)
    text = text.replace('NO.', '').replace('0..39', '0.39')
    if re.match(r'^[<>≤≥]', text):
        return None, '检出限/范围值'
    try:
        value = float(text)
        if not math.isfinite(value):
            return None, '非有限数值'
        return value, ('按PBI规则换算' if text != str(raw).strip() else '数值')
    except ValueError:
        return None, '文本结果'


def record(row, origin):
    r = {k.upper(): v for k, v in row.items()}
    value, qualifier = numeric(r.get('FINAL'))
    def stamp(v):
        if v is None:
            return None
        text = str(v).replace('T', ' ')
        return text + ' 00:00:00' if len(text) == 10 else text
    analyte = str(r.get('ANALYTE') or '')
    return (origin, r.get('PLANTID') or '', r.get('PROGNAME') or '',
            analyte.replace('喹啉不溶物W(%)', '喹啉不溶物'), analyte,
            stamp(r.get('SAMPDATE')), stamp(r.get('DATEENTER')),
            r.get('SAMPLE_TYPE') or '', r.get('UNITS') or '', r.get('FINAL'),
            value, qualifier, json.dumps(row, ensure_ascii=False, default=str))


INSERT = '''INSERT INTO results(origin,plant,sample,analyte,analyte_raw,sampled_at,
 entered_at,sample_type,unit,result_raw,value,qualifier,raw_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)'''


def status():
    try:
        sync = json.loads(STATUS.read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        sync = {'state': 'not_started'}
    with closing(connect()) as db:
        coverage = [dict(r) for r in db.execute('''SELECT origin,count(*) records,
          count(DISTINCT plant) plants,min(sampled_at) first_sample,max(sampled_at) last_sample
          FROM results GROUP BY origin''')]
        catalog = db.execute("SELECT value FROM metadata WHERE key='plants'").fetchone()
        available_plants = len(set(json.loads(catalog[0]) if catalog else []) |
                               {r[0] for r in db.execute("SELECT DISTINCT plant FROM results WHERE origin='live' AND plant<>''")})
    try:
        backfill = json.loads((DATA / 'lims_backfill_status.json').read_text(encoding='utf-8'))
    except (FileNotFoundError, json.JSONDecodeError):
        backfill = None
    if backfill and backfill['state'] == 'running':
        sync = dict(sync, state='fetching', completed_windows=backfill.get('completed_windows', 0), max_years=10,
                    downloaded=backfill.get('total_records', 0) + (sync.get('downloaded', 0) if sync.get('state')=='fetching' else 0))
    # Credentials never form part of status or public configuration.
    return {'sync': sync, 'coverage': coverage, 'settings': settings(), 'available_plants': available_plants, 'backfill': backfill,
            'sync_enabled': os.environ.get('LIMS_ENABLE_SYNC','false').lower() == 'true'}


def conditions(params, omit=None):
    clauses, args = ['origin=?'], [params.get('origin', ['live'])[0]]
    if args[0] not in ('live', 'pbix'):
        raise ValueError('数据来源无效')
    for key, column in FIELDS.items():
        selected = params.get(key, [])
        if selected and key != omit:
            clauses.append(f'{column} IN ({",".join("?" for _ in selected)})')
            args.extend(selected)
    for key, op in [('start', '>='), ('end', '<')]:
        val = params.get(key, [''])[0]
        if val:
            date = datetime.strptime(val, '%Y-%m-%d')
            if key == 'end':
                date += timedelta(days=1)
            clauses.append(f'sampled_at {op} ?')
            args.append(date.isoformat(sep=' '))
    if params.get('start', [''])[0] and params.get('end', [''])[0] and params['start'][0] > params['end'][0]:
        raise ValueError('开始日期不能晚于结束日期')
    return ' AND '.join(clauses), args


def options(params):
    with closing(connect()) as db:
        result = {}
        for key, column in FIELDS.items():
            # Device inventory remains global; other facets reflect the current selection.
            subset = {'origin': params.get('origin', ['live'])} if key == 'plant' else params
            where, args = conditions(subset, omit=key)
            result[key] = [r[0] for r in db.execute(f'SELECT DISTINCT {column} FROM results WHERE {where} ORDER BY {column}', args)]
            if key == 'plant' and subset['origin'][0] == 'live':
                catalog = db.execute("SELECT value FROM metadata WHERE key='plants'").fetchone()
                if catalog:
                    result[key] = sorted(set(result[key]) | set(json.loads(catalog[0])))
        return result


def query(params):
    where, args = conditions(params)
    page = max(1, int(params.get('page', ['1'])[0]))
    with closing(connect()) as db:
        summary = dict(db.execute(f'''SELECT count(*) records,count(value) numeric_records,
          min(sampled_at) first_sample,max(sampled_at) last_sample,
          min(value) minimum,max(value) maximum,avg(value) average
          FROM results WHERE {where}''', args).fetchone())
        # Group by device/sample/item/unit so unrelated samples and units never get averaged together.
        points = [dict(r) for r in db.execute(f'''SELECT plant,sample,analyte,unit,sampled_at,
          avg(value) value,count(*) records,count(value) numeric_records
          FROM results WHERE {where}
          GROUP BY plant,sample,analyte,unit,sampled_at ORDER BY sampled_at LIMIT 30001''', args)]
        rows = [dict(r) for r in db.execute(f'''SELECT id,plant,sample,analyte,analyte_raw,
          sampled_at,entered_at,sample_type,unit,result_raw,value,qualifier
          FROM results WHERE {where} ORDER BY sampled_at DESC,id DESC LIMIT 100 OFFSET ?''', args + [(page-1)*100])]
    truncated = len(points) > 30000
    return {'summary': summary, 'points': [] if truncated else points, 'too_many_points': truncated,
            'rows': rows, 'page': page, 'page_size': 100}


def start_sync(days, date_basis):
    global PROCESS
    days = int(days)
    if not 1 <= days <= 3650:
        raise ValueError('采集天数必须为1至3650')
    if date_basis not in ('DATEENTER', 'sampdate'):
        raise ValueError('采集时间字段无效')
    with LOCK:
        with collection_guard():
            pass
        if PROCESS is not None and PROCESS.poll() is None:
            raise ValueError('正在采集，请等待当前任务完成')
        cfg = settings()
        cfg.update(days=days, date_basis=date_basis)
        CONFIG.write_text(json.dumps(cfg, ensure_ascii=False, indent=2), encoding='utf-8')
        env = os.environ.copy()
        env['PYTHONIOENCODING'] = 'utf-8'
        STATUS.write_text(json.dumps({'state': 'starting', 'days': days}, ensure_ascii=False), encoding='utf-8')
        (ROOT / 'logs').mkdir(exist_ok=True)
        log = open(ROOT / 'logs/lims_sync.log', 'a', encoding='utf-8')
        try:
            PROCESS = subprocess.Popen([sys.executable, str(ROOT / 'tools/lims_collector.py'), '--days', str(days), '--date-basis', date_basis],
                cwd=ROOT, env=env, stdout=log, stderr=log,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        except OSError:
            STATUS.write_text(json.dumps({'state': 'failed', 'error': '无法启动Python 3.12采集器，请检查本机安装。'}, ensure_ascii=False), encoding='utf-8')
            raise ValueError('无法启动采集器')
        finally:
            log.close()
    return {'started': True, 'days': days, 'date_basis': date_basis}
