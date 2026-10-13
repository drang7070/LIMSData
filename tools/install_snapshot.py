"""Explicitly merge the shipped, verified acquisition window into an existing DB.

Never replace older history or newer local data. A consistent full backup is saved
before the atomic window merge. Run with the web service stopped.
"""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import sys
from contextlib import closing
from datetime import datetime
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lims import core


def install():
    seed=core.ROOT/'seed'
    manifest=json.loads((seed/'manifest.json').read_text(encoding='utf-8'))
    sync=json.loads((seed/'lims_sync_status.json').read_text(encoding='utf-8'))
    if sync.get('state')!='complete' or sync.get('date_basis')!='DATEENTER':
        raise RuntimeError('快照不含已核对的录入时间更新窗口')
    archive=seed/'lims_history.sqlite3.gz'
    with archive.open('rb') as stream:
        if hashlib.file_digest(stream,'sha256').hexdigest()!=manifest['gzip_sha256']:
            raise RuntimeError('快照压缩包校验失败')
    temporary=core.DATA/'snapshot_import.tmp'
    with core.collection_guard():
        try:
            with gzip.open(archive,'rb') as source,temporary.open('wb') as target:
                shutil.copyfileobj(source,target,1024*1024)
            with temporary.open('rb') as stream:
                if hashlib.file_digest(stream,'sha256').hexdigest()!=manifest['sha256']:
                    raise RuntimeError('解压快照校验失败')
            with closing(sqlite3.connect(temporary)) as source:
                if source.execute('PRAGMA quick_check').fetchone()[0]!='ok':
                    raise RuntimeError('快照完整性校验失败')
                if source.execute('SELECT count(*) FROM results').fetchone()[0]!=manifest['records']:
                    raise RuntimeError('快照记录数不符')
                count=source.execute("SELECT count(*) FROM results WHERE origin='live' AND entered_at>=? AND entered_at<?",[sync['start'],sync['end']]).fetchone()[0]
                if count!=sync['source_count']:
                    raise RuntimeError('快照更新窗口未通过源库数量校验')
            if not core.DB.exists():
                os.replace(temporary,core.DB)
            else:
                with closing(core.connect()) as target:
                    current=target.execute("SELECT value FROM metadata WHERE key='last_live_sync'").fetchone()
                    if current and json.loads(current[0]).get('end','')>sync['end']:
                        raise RuntimeError('运行库已有更新的数据，不导入较旧快照')
                    backup=core.DATA/('lims_backup_'+datetime.now().strftime('%Y%m%d_%H%M%S_%f')+'.sqlite3')
                    with closing(sqlite3.connect(backup)) as saved:
                        target.backup(saved)
                    target.execute('ATTACH DATABASE ? AS shipped',[str(temporary)])
                    cols='origin,plant,sample,analyte,analyte_raw,sampled_at,entered_at,sample_type,unit,result_raw,value,qualifier,raw_json'
                    with target:
                        target.execute("DELETE FROM results WHERE origin='live' AND entered_at>=? AND entered_at<?",[sync['start'],sync['end']])
                        target.execute(f"INSERT INTO results({cols}) SELECT {cols} FROM shipped.results WHERE origin='live' AND entered_at>=? AND entered_at<?",[sync['start'],sync['end']])
                        target.execute("INSERT OR REPLACE INTO metadata SELECT key,value FROM shipped.metadata WHERE key IN ('plants','last_live_sync')")
                    target.execute('DETACH DATABASE shipped')
                    print('已保存更新前备份：'+str(backup))
            (core.DATA/'lims_sync_status.json').write_text(json.dumps(sync,ensure_ascii=False,indent=2),encoding='utf-8')
            print(f"已合并 {sync['start']} 至 {sync['end']} 窗口，共 {count} 条；其他时间段保留。")
        finally:
            temporary.unlink(missing_ok=True)


if __name__=='__main__':
    install()
