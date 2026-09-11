"""Restore the shipped database once. Existing server data is never overwritten."""
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
from contextlib import closing
from pathlib import Path
from lims import core


def bootstrap():
    with core.collection_guard():
        if core.DB.exists():
            with closing(sqlite3.connect(core.DB)) as db:
                db.execute('SELECT count(*) FROM results').fetchone()
            return
        seed = core.ROOT / 'seed'
        manifest = json.loads((seed / 'manifest.json').read_text(encoding='utf-8'))
        archive = seed / 'lims_history.sqlite3.gz'
        with archive.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != manifest['gzip_sha256']:
                raise RuntimeError('数据库压缩包SHA256校验失败')
        temporary = core.DB.with_suffix('.restore.tmp')
        with gzip.open(archive,'rb') as src, temporary.open('wb') as dst:
            shutil.copyfileobj(src,dst,1024*1024)
        with temporary.open('rb') as stream:
            if hashlib.file_digest(stream,'sha256').hexdigest() != manifest['sha256']:
                raise RuntimeError('解压数据库SHA256校验失败')
        db = sqlite3.connect(temporary)
        try:
            if db.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
                raise RuntimeError('SQLite完整性检查失败')
            if db.execute('SELECT count(*) FROM results').fetchone()[0] != manifest['records']:
                raise RuntimeError('数据库记录数检查失败')
        finally:
            db.close()
        os.replace(temporary,core.DB)
        for name in ['lims_sync_status.json','lims_backfill_status.json','lims_backfill_acceptance.json']:
            if not (core.DATA / name).exists():
                shutil.copyfile(seed / name,core.DATA / name)


if __name__ == '__main__':
    bootstrap()
    print('数据库已就绪')
