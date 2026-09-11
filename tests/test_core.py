import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch
from contextlib import closing
from lims import core as lims


class LimsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.original_connect = lims.connect
        self.path = Path(self.temp.name) / 'lims.sqlite3'
        self.override = patch.object(lims, 'connect', lambda: self.original_connect(self.path))
        self.override.start()

    def tearDown(self):
        self.override.stop()
        self.temp.cleanup()

    def insert(self, **updates):
        row = {'PLANTID': '三排送', 'progname': '3P-1402', 'ANALYTE': '喹啉不溶物W(%)',
               'sampdate': date(2026,9,1), 'DATEENTER': datetime(2026,9,2), 'FINAL':'1.53', 'UNITS':'% w/w'}
        row.update(updates)
        with closing(lims.connect()) as db:
            db.execute(lims.INSERT, lims.record(row, 'live'))
            db.commit()

    def test_date_boundary_includes_both_days(self):
        for day in [1,11,12]:
            self.insert(sampdate=date(2026,9,day))
        result=lims.query({'start':['2026-09-01'],'end':['2026-09-11']})
        self.assertEqual(result['summary']['records'],2)

    def test_raw_values_qualifiers_and_invalid(self):
        for raw in ['无效','不成线','nan','<0.01','检验中']:
            self.assertIsNone(lims.numeric(raw)[0])
        for raw,value in [('痕迹',.02),('少量',.08),('未检出',0),('2.14.',2.14),('0..39',.39)]:
            self.assertEqual(lims.numeric(raw)[0],value)
        self.insert(FINAL='无效')
        r=lims.query({})['rows'][0]
        self.assertEqual(r['result_raw'],'无效')
        self.assertEqual(r['analyte_raw'],'喹啉不溶物W(%)')
        self.assertIsNone(r['value'])

    def test_average_retains_duplicates_separates_samples_units(self):
        self.insert(FINAL='1')
        self.insert(FINAL='3')
        self.insert(FINAL='9',UNITS='mg/L')
        self.insert(FINAL='20',progname='another')
        r=lims.query({})
        self.assertEqual(r['summary']['records'],4)
        self.assertEqual(len(r['points']),3)
        self.assertEqual(next(p['value'] for p in r['points'] if p['sample']=='3P-1402' and p['unit']=='% w/w'),2)

    def test_facets_keep_all_devices_and_parameterize_filters(self):
        self.insert()
        self.insert(PLANTID='其他装置',progname='other')
        o=lims.options({'plant':['三排送']})
        self.assertEqual(len(o['plant']),2)
        self.assertEqual(o['sample'],['3P-1402'])
        self.assertEqual(lims.query({'sample':["' OR 1=1 --"]})['summary']['records'],0)

    def test_invalid_range_and_empty_unit(self):
        self.insert(UNITS='')
        self.assertEqual(lims.query({'unit':['']})['summary']['records'],1)
        with self.assertRaises(ValueError):
            lims.query({'start':['2026-09-11'],'end':['2026-09-01']})

    def test_half_year_windows_have_no_gaps_and_ten_year_limit(self):
        from tools.lims_backfill import windows, shift_months
        for anchor in [datetime(2026,9,11,2,0), datetime(2024,2,29), datetime(2026,8,31)]:
            parts=windows(anchor)
            self.assertEqual(len(parts),20)
            self.assertEqual(parts[0][1],anchor)
            self.assertEqual(parts[-1][0],shift_months(anchor,-120))
            for i,(start,end) in enumerate(parts):
                self.assertLess(start,end)
                if i:
                    self.assertEqual(end,parts[i-1][0])

    def test_collection_guard_rejects_parallel_writer(self):
        root = Path(self.temp.name)
        (root/'data').mkdir()
        with patch.object(lims,'DATA',root/'data'):
            with lims.collection_guard():
                with self.assertRaises(ValueError):
                    with lims.collection_guard():
                        pass


if __name__ == '__main__':
    unittest.main()
