"""Explainable specification checks and individual-observation SPC alerts."""
from __future__ import annotations

import json
import math
import re
import threading
import time
import unicodedata
from collections import deque
from contextlib import closing
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from . import core

SPEC_PATH = Path(__file__).resolve().parents[1] / 'config/quality_specs.json'
KEYS = ('plant', 'sample', 'analyte', 'unit', 'sample_type')
SPC_RULES = {
    'R1': '单点超过中心线±3σ', 'R2': '连续3点中至少2点同侧超过2σ',
    'R3': '连续5点中至少4点同侧超过1σ', 'R4': '连续8点位于中心线同侧',
    'R5': '连续6点严格递增或递减', 'R6': '连续14点交替升降',
}
SPC_SOURCE = 'https://www.itl.nist.gov/div898/handbook/pmc/section3/pmc32.htm'
_CACHE = {}
_LOCK = threading.Lock()


def china_today():
    return datetime.now(timezone(timedelta(hours=8))).date()


def normalized(text):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', str(text or ''))).lower()


def unit_name(unit):
    value = normalized(unit).replace('{', '').replace('}', '').replace('³', '3')
    return {'%w/w': '%', 'w/%': '%', 'wt%': '%', '%(w/w)': '%',
            'g/ml': 'g/cm3', '°c': '°c', '℃': '°c'}.get(value, value)


def factor(source, target):
    """Only explicit physical conversions; normal and actual gas volume differ."""
    source, target = unit_name(source), unit_name(target)
    if source == target:
        return 1.0
    if target == '1' and source in ('', '1'):
        return 1.0
    return {('g/m3', 'mg/m3'): 1000, ('mg/m3', 'g/m3'): .001,
            ('kg/m3', 'g/cm3'): .001, ('g/cm3', 'kg/m3'): 1000}.get((source, target))


def specifications():
    return json.loads(SPEC_PATH.read_text(encoding='utf-8'))


def matches(rule, key):
    # Explicit aliases, including device and position, prevent cross-product limits.
    return all(normalized(key[field]) in {normalized(v) for v in rule[plural]}
               for field, plural in [('plant', 'plants'), ('sample', 'samples'), ('analyte', 'analytes')])


def limits_text(rule):
    if rule.get('ranges'):
        return ' 或 '.join(f'≥{lo:g}' if hi is None else f'{lo:g}–{hi:g}' for lo, hi in rule['ranges']) + ' ' + rule['unit']
    lo, hi = rule.get('lower'), rule.get('upper')
    value = f'{lo:g}–{hi:g}' if lo is not None and hi is not None else f'≥{lo:g}' if lo is not None else f'≤{hi:g}'
    return value + ' ' + rule['unit']


def check_spec(row, applicable):
    reasons, pending = [], []
    for rule in applicable:
        label = f"{rule['id']} 第{rule['page']}页 · {limits_text(rule)}"
        if rule.get('override_source'):
            label += ' · ' + rule['override_source']
        if row['sampled_at'][:10] < rule['effective']:
            pending.append(label + '：标准尚未生效')
            continue
        if rule.get('condition'):
            pending.append(label + '：' + rule['condition'])
            continue
        raw = str(row.get('result_raw') or '').strip()
        # Qualitative acceptance is checked against the source text, not PBI's numbers.
        if raw in rule.get('allowed_text', []):
            continue
        if rule.get('allowed_text') and raw in ('痕迹','微量','少量','大量'):
            reasons.append({'kind':'spec','code':rule['id'],
                            'message':f"原始定性结果 {raw} 不在允许范围（{'、'.join(rule['allowed_text'])}）",'source':label})
            continue
        value = literal_value(row)
        if value is None:
            pending.append(label + '：文本、范围值或无效结果待复核')
            continue
        scale = factor(row['unit'], rule['unit'])
        if scale is None:
            pending.append(label + f"：单位未确认（源库 {row['unit'] or '空白'}）")
            continue
        value *= scale
        ranges = rule.get('ranges', [[rule.get('lower'), rule.get('upper')]])
        if not any((lo is None or value >= lo) and (hi is None or value <= hi) for lo, hi in ranges):
            reasons.append({'kind': 'spec', 'code': rule['id'], 'message': f"原始结果 {raw} {row['unit']} 超出 {limits_text(rule)}", 'source': label})
    return reasons, pending


def literal_value(row):
    try:
        value = float(str(row.get('result_raw') or '').strip())
        return value if math.isfinite(value) else None
    except (TypeError, ValueError):
        return None


def spc_signals(values, center, sigma):
    """Signals belong to the final point of each completed rule window."""
    if not values:
        return []
    tolerance = max(1e-12, abs(center)*1e-12)
    if sigma <= tolerance:
        return ['ZERO'] if abs(values[-1]-center) > tolerance else []
    hits = []
    if abs(values[-1]-center) > 3*sigma:
        hits.append('R1')
    for n, required, level, code in [(3,2,2,'R2'), (5,4,1,'R3'), (8,8,0,'R4')]:
        if len(values) >= n and any(sum(sign*(v-center) > level*sigma for v in values[-n:]) >= required for sign in (-1,1)):
            hits.append(code)
    if len(values) >= 6:
        differences = [b-a for a,b in zip(values[-6:], values[-5:])]
        if all(d>0 for d in differences) or all(d<0 for d in differences):
            hits.append('R5')
    if len(values) >= 14:
        differences = [b-a for a,b in zip(values[-14:], values[-13:])]
        if all(a*b<0 for a,b in zip(differences, differences[1:])):
            hits.append('R6')
    return hits


def assess(rows, key, config=None):
    config = config or specifications()
    applicable = [r for r in config['rules'] if matches(r, key)]
    by_stamp = {}
    for row in rows:
        by_stamp.setdefault(row['sampled_at'], []).append(row)
    baseline, sequence = deque(maxlen=30), deque(maxlen=14)
    points = []
    for stamp, records in sorted(by_stamp.items()):
        reasons, pending = [], []
        for row in records:
            r, p = check_spec(row, applicable)
            reasons.extend(r); pending.extend(p)
        numeric = [v for row in records if (v := literal_value(row)) is not None]
        value = sum(numeric)/len(numeric) if numeric else None
        point = dict(sampled_at=stamp, value=value, records=len(records),
                     result_raw=' / '.join(str(r.get('result_raw') or '空白') for r in records),
                     reasons=reasons, pending=sorted(set(pending)), baseline=None)
        if value is not None:
            if len(baseline) >= 20:
                values = list(baseline)
                center = sum(values)/len(values)
                sigma = sum(abs(b-a) for a,b in zip(values,values[1:]))/(len(values)-1)/1.128
                point['baseline'] = dict(center=center, sigma=sigma, lower=center-3*sigma,
                                         upper=center+3*sigma, count=len(values))
                for code in spc_signals([*sequence, value], center, sigma):
                    message = SPC_RULES.get(code, '历史基线无波动，本点偏离常数基线')
                    reasons.append(dict(kind='spc', code=code, message=message,
                                        source=f'此前{len(values)}个数值时刻；CL={center:.6g}，σ={sigma:.6g}'))
            else:
                point['pending'].append(f'SPC历史不足：此前{len(baseline)}个数值时刻，至少需要20个')
            baseline.append(value); sequence.append(value)
        else:
            sequence.clear()
            point['pending'].append('此时刻无原始数值，SPC不判定')
        if not applicable:
            point['pending'].append('未匹配质量限值，仅执行可用的SPC规则')
        point['abnormal'] = bool(reasons)
        points.append(point)
    return points, applicable


def series_where(key):
    return 'origin=? AND ' + ' AND '.join(f'{field}=?' for field in KEYS), ['live', *[key[f] for f in KEYS]]


def severity(points):
    """Comparable cross-unit priority from recent anomalous points only."""
    scored=[]
    for point in points:
        baseline=point.get('baseline')
        if baseline and point['value'] is not None:
            tolerance=max(1e-12,abs(baseline['center'])*1e-12)
            if baseline['sigma']>tolerance:
                z=abs(point['value']-baseline['center'])/baseline['sigma']
                if math.isfinite(z):
                    scored.append((z,point['sampled_at'],point))
    codes=sorted({r['code'] for p in points for r in p['reasons'] if r['kind']=='spc'})
    worst=max(scored,key=lambda s:(s[0],s[1])) if scored else None
    return dict(max_sigma=worst[0] if worst else None,spc_rule_count=len(codes),
                spc_codes=codes,point=worst[2] if worst else points[-1])


def sort_items(items):
    # Quality violations always precede SPC-only alerts, across all pages.
    items.sort(key=lambda s:(
        'spec' in s['kinds'],
        s['severity']['max_sigma'] if s['severity']['max_sigma'] is not None else -1,
        s['severity']['spc_rule_count'],s['abnormal_points'],s['last_abnormal'],
        s['plant'],s['sample'],s['analyte'],s['unit'],s['sample_type']),reverse=True)


def load_series(db, key, start, end):
    where, args = series_where(key)
    cols = 'id,sampled_at,result_raw,value,unit'
    # Earlier context is independent of the chart's display range.
    prior = [dict(r) for r in db.execute(f'SELECT {cols} FROM results WHERE {where} AND sampled_at<? ORDER BY sampled_at DESC,id DESC LIMIT 2000', args+[start])]
    rows = [dict(r) for r in db.execute(f'SELECT {cols} FROM results WHERE {where} AND sampled_at>=? AND sampled_at<? ORDER BY sampled_at,id LIMIT 10001',args+[start,end])]
    if len(rows) > 10000:
        raise ValueError('该系列超过10,000条原始记录，请缩小趋势日期范围')
    return sorted(prior+rows, key=lambda r:(r['sampled_at'],r['id']))


def _report(today, db_factory):
    start, end = (today-timedelta(days=6)).isoformat(), (today+timedelta(days=1)).isoformat()
    config = specifications()
    items, pending, count = [], [], 0
    with closing(db_factory()) as db:
        latest = db.execute("SELECT max(sampled_at) FROM results WHERE origin='live'").fetchone()[0]
        plants = {r[0] for r in db.execute("SELECT DISTINCT plant FROM results WHERE origin='live' AND plant<>''")}
        catalog = db.execute("SELECT value FROM metadata WHERE key='plants'").fetchone()
        if catalog:
            plants.update(json.loads(catalog[0]))
        recent = [dict(r) for r in db.execute('SELECT '+','.join(KEYS)+",count(*) records FROM results WHERE origin='live' AND sampled_at>=? AND sampled_at<? GROUP BY "+','.join(KEYS),[start,end])]
        for key in recent:
            count += key.pop('records')
            points, applicable = assess(load_series(db,key,start,end), key, config)
            targets = [p for p in points if start <= p['sampled_at'] < end]
            bad = [p for p in targets if p['abnormal']]
            statuses = sorted({v for p in targets for v in p['pending']})
            if statuses:
                pending.append(dict(**key, messages=statuses))
            if bad:
                kinds = sorted({r['kind'] for p in bad for r in p['reasons']})
                items.append(dict(**key, abnormal_points=len(bad), last_abnormal=bad[-1]['sampled_at'],
                                  severity=severity(bad),
                                  latest=targets[-1], kinds=kinds, reasons=bad[-1]['reasons'],
                                  points=points[-10:], specs=[dict(r,limits=limits_text(r)) for r in applicable],
                                  pending=statuses))
    sort_items(items)
    return dict(window_start=start, window_end=today.isoformat(), latest_sample=latest,
                stale=not recent, recent_records=count, checked_series=len(recent),
                plants=sorted(plants),items=items, pending_series=pending, product_status=config['product_status'])


def report(params, *, today=None, db_factory=None):
    today = today or china_today()
    db_factory = db_factory or core.connect
    # Cache only the unfiltered report and invalidate on SQLite/WAL changes.
    cache_key = None
    if db_factory is core.connect or db_factory.__module__ == 'mes_agent.lims':
        database = db_factory.__globals__['DB']
        paths = [database, Path(str(database)+'-wal'), SPEC_PATH]
        cache_key = (today.isoformat(), tuple((str(p),p.stat().st_mtime_ns,p.stat().st_size) for p in paths if p.exists()))
    with _LOCK:
        cached = _CACHE.get('report')
        if cache_key and cached and cached[0]==cache_key and time.monotonic()-cached[1]<60:
            data = cached[2]
        else:
            data = _report(today,db_factory)
            if cache_key:
                _CACHE['report']=(cache_key,time.monotonic(),data)
    items = data['items']
    plant = params.get('plant',[''])[0]
    search = params.get('search',[''])[0].strip().lower()
    kind = params.get('kind',[''])[0]
    if kind not in ('','spec','spc'):
        raise ValueError('异常类型无效')
    filtered = [s for s in items if (not plant or s['plant']==plant) and
                (not kind or kind in s['kinds']) and
                (not search or search in (s['sample']+' '+s['analyte']).lower())]
    page = max(1,int(params.get('page',['1'])[0]))
    return {**{k:v for k,v in data.items() if k not in ('items','pending_series')},
            'total':len(filtered),
            'abnormal_series':len(items), 'pending_count':len(data['pending_series']),
            'pending_series':data['pending_series'][:100], 'pending_truncated':len(data['pending_series'])>100,
            'page':page, 'page_size':12, 'items':filtered[(page-1)*12:page*12]}


def trend(params, *, today=None, db_factory=None):
    today = today or china_today()
    key = {field:params.get(field,[''])[0] for field in KEYS}
    if not key['plant'] or not key['sample'] or not key['analyte']:
        raise ValueError('请选择完整装置、样品和分析项')
    start, end = params.get('start',[''])[0], params.get('end',[''])[0]
    if bool(start) != bool(end):
        raise ValueError('请同时填写开始和结束日期')
    if start:
        first, last = date.fromisoformat(start),date.fromisoformat(end)
        if first>last or (last-first).days>3653 or last>today:
            raise ValueError('趋势日期须先后有序、不超过10年且不晚于今天')
    else:
        first,last=today-timedelta(days=3653),today
    with closing((db_factory or core.connect)()) as db:
        if not start:
            where,args=series_where(key)
            stamps=[r[0] for r in db.execute(f'SELECT DISTINCT sampled_at FROM results WHERE {where} AND sampled_at>=? AND sampled_at<? ORDER BY sampled_at DESC LIMIT 10',args+[first.isoformat(),(last+timedelta(days=1)).isoformat()])]
            if not stamps:
                return dict(**key,points=[],specs=[],mode='latest10')
            first=date.fromisoformat(stamps[-1][:10])
        points,applicable=assess(load_series(db,key,first.isoformat(),(last+timedelta(days=1)).isoformat()),key)
    shown=[p for p in points if first.isoformat()<=p['sampled_at']<(last+timedelta(days=1)).isoformat()]
    if not start:
        shown=shown[-10:]
    return dict(**key,points=shown,specs=[dict(r,limits=limits_text(r)) for r in applicable],mode='range' if start else 'latest10')


def rules_info():
    config=specifications()
    return dict(config, rules=[dict(r,limits=limits_text(r)) for r in config['rules']],
                spc_rules=SPC_RULES,spc_source=SPC_SOURCE,
                baseline='同装置/样品/分析项/单位/类型；同取样时刻按原始数值均值；此前最多30个数值时刻，至少20个，σ=平均移动极差/1.128。当前点不参与基线。')
