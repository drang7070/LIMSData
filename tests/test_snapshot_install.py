import gzip
import hashlib
import json
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch
from lims import core
from tools import install_snapshot


class SnapshotInstallTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.root=Path(self.tmp.name)
        (self.root/'seed').mkdir();(self.root/'data').mkdir()
        self.db=self.root/'data/lims_history.sqlite3'
        self.original_connect=core.connect
        self.patches=[patch.object(core,'ROOT',self.root),patch.object(core,'DATA',self.root/'data'),
                      patch.object(core,'DB',self.db),patch.object(core,'connect',lambda:self.original_connect(self.db))]
        for p in self.patches:p.start()
        source=self.root/'source.sqlite3'
        with closing(self.original_connect(source)) as d:
            self.insert(d,'2026-10-08','7')
            d.commit()
        blob=source.read_bytes()
        zipped=gzip.compress(blob)
        (self.root/'seed/lims_history.sqlite3.gz').write_bytes(zipped)
        (self.root/'seed/manifest.json').write_text(json.dumps(dict(records=1,sha256=hashlib.sha256(blob).hexdigest(),gzip_sha256=hashlib.sha256(zipped).hexdigest())),encoding='utf-8')
        (self.root/'seed/lims_sync_status.json').write_text(json.dumps(dict(state='complete',date_basis='DATEENTER',start='2026-10-07',end='2026-10-13',source_count=1)),encoding='utf-8')

    def tearDown(self):
        for p in reversed(self.patches):p.stop()
        self.tmp.cleanup()

    def insert(self,d,stamp,value):
        d.execute(core.INSERT,core.record(dict(PLANTID='三排送',progname='3P-1402',ANALYTE='水份',UNITS='%',FINAL=value,sampdate=stamp,DATEENTER=stamp),'live'))

    def test_merge_preserves_other_windows_and_creates_backup(self):
        with closing(core.connect()) as d:
            self.insert(d,'2026-09-01','1');self.insert(d,'2026-10-08','2');self.insert(d,'2026-10-14','3');d.commit()
        install_snapshot.install()
        with closing(core.connect()) as d:
            self.assertEqual([r[0] for r in d.execute('SELECT value FROM results ORDER BY sampled_at')],[1,7,3])
        self.assertEqual(len(list((self.root/'data').glob('lims_backup_*.sqlite3'))),1)

    def test_newer_sync_refused(self):
        with closing(core.connect()) as d:
            d.execute('INSERT INTO metadata VALUES (?,?)',['last_live_sync',json.dumps(dict(end='2026-10-15'))]);d.commit()
        with self.assertRaisesRegex(RuntimeError,'更新'):
            install_snapshot.install()

    def test_corrupt_archive_refused(self):
        (self.root/'seed/lims_history.sqlite3.gz').write_bytes(b'bad')
        with self.assertRaisesRegex(RuntimeError,'校验'):
            install_snapshot.install()
        self.assertFalse(self.db.exists())
