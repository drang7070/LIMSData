"""Resume half-year windows, newest first, for at most ten calendar years."""
from __future__ import annotations
import calendar
import json
import sys
import time
from contextlib import closing
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from tools.lims_collector import _collect, source_connection, write_status
from lims import core as lims

MANIFEST = lims.DATA / 'lims_backfill_status.json'


def shift_months(value, months):
    index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(index, 12)
    month = month0 + 1
    return value.replace(year=year, month=month, day=min(value.day, calendar.monthrange(year, month)[1]))


def windows(anchor):
    # Always derive boundaries from anchor, avoiding month-end drift.
    return [(shift_months(anchor, -6*(n+1)), shift_months(anchor, -6*n)) for n in range(20)]


def save(state):
    state['updated_at'] = str(datetime.now())
    tmp = MANIFEST.with_suffix('.tmp')
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding='utf-8')
    tmp.replace(MANIFEST)


def run():
    with lims.collection_guard():
        if MANIFEST.exists():
            state = json.loads(MANIFEST.read_text(encoding='utf-8'))
            anchor = datetime.fromisoformat(state['anchor'])
        else:
            with closing(source_connection()) as src:
                anchor = src.execute('SELECT GETDATE()').fetchone()[0]
            state = {'state': 'running', 'anchor': str(anchor), 'limit_start': str(shift_months(anchor,-120)),
                     'date_basis': 'DATEENTER', 'null_dateenter_fallback': 'sampdate', 'step_months': 6, 'max_years': 10,
                     'windows': [{'start': str(a), 'end': str(b), 'state': 'pending'} for a,b in windows(anchor)]}
        state['state'] = 'running'
        save(state)
        for index, window in enumerate(state['windows']):
            if window['state'] == 'complete':
                continue
            start, end = map(datetime.fromisoformat, (window['start'], window['end']))
            window['state'] = 'running'
            state['current_window'] = index+1
            save(state)
            print(json.dumps({'window': index+1, 'start': str(start), 'end': str(end)}, ensure_ascii=False), flush=True)
            for attempt in range(1,4):
                result = _collect(days=(end-start).days+1, date_basis='DATEENTER', start=start, end=end, chunk_days=190, fallback_sample_date=True)
                if result == 0:
                    break
                window['attempt'] = attempt
                save(state)
                if attempt < 3:
                    time.sleep(5)
            if result:
                window['state'] = 'failed'
                state['state'] = 'failed'
                save(state)
                return 1
            sync = json.loads(lims.STATUS.read_text(encoding='utf-8'))
            with closing(lims.connect()) as db:
                local_count = db.execute("SELECT count(*) FROM results WHERE origin='live' AND COALESCE(entered_at,sampled_at)>=? AND COALESCE(entered_at,sampled_at)<?", (str(start),str(end))).fetchone()[0]
            if local_count != sync['source_count']:
                raise RuntimeError('发布后本地条数核对失败')
            window.update(state='complete', source_count=sync['source_count'], local_count=local_count,
                          verified_at=str(datetime.now()))
            state['completed_windows'] = index+1
            state['total_records'] = sum(w.get('local_count',0) for w in state['windows'])
            save(state)
            print(json.dumps({'window_complete': index+1, 'records': local_count, 'total': state['total_records']}), flush=True)
        with closing(lims.connect()) as db:
            count = db.execute("SELECT count(*) FROM results WHERE origin='live' AND COALESCE(entered_at,sampled_at)>=? AND COALESCE(entered_at,sampled_at)<?",(state['limit_start'],state['anchor'])).fetchone()[0]
            if count != state['total_records']:
                raise RuntimeError('十年窗口汇总条数不一致')
        state.update(state='complete', verified_total=count)
        save(state)
        write_status(state='complete', origin='live', date_basis='DATEENTER', start=state['limit_start'], end=state['anchor'],
                     downloaded=count, source_count=count, step_months=6, completed_windows=20, max_years=10)
        return 0


if __name__ == '__main__':
    try:
        sys.exit(run())
    except Exception as exc:
        # Preserve completed windows for resume. Do not log SQL/credential details.
        if MANIFEST.exists() and not isinstance(exc, ValueError):
            failed = json.loads(MANIFEST.read_text(encoding='utf-8'))
            failed.update(state='failed', error_type=type(exc).__name__)
            save(failed)
        print(json.dumps({'success': False, 'error_type': type(exc).__name__}, ensure_ascii=False), flush=True)
        sys.exit(1)
