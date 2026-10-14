import tempfile
import unittest
from contextlib import closing
from datetime import date, timedelta
from pathlib import Path
from lims import anomalies as a, core


class AnomalyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.dbpath=Path(self.tmp.name)/'test.sqlite3'
        self.connect=lambda:core.connect(self.dbpath)
        self.key=dict(plant='三排送',sample='3P-1402',analyte='水份（m）',unit='%',sample_type='中间品')

    def tearDown(self):
        self.tmp.cleanup()

    def insert(self,stamp,value,**fields):
        key={**self.key,**fields}
        row=dict(PLANTID=key['plant'],progname=key['sample'],ANALYTE=key['analyte'],
                 UNITS=key['unit'],SAMPLE_TYPE=key['sample_type'],sampdate=stamp,FINAL=str(value))
        with closing(self.connect()) as db:
            db.execute(core.INSERT,core.record(row,'live'));db.commit()

    def test_spc_rules_and_boundaries(self):
        examples={'R1':[0,0,3.01],'R2':[2.1,0,2.2],
                  'R3':[1.1,1.2,0,1.3,1.4],'R4':[.1]*8,
                  'R5':[0,.1,.2,.3,.4,.5],'R6':[.1,-.1]*7}
        for rule,values in examples.items():
            self.assertIn(rule,a.spc_signals(values,0,1),rule)
        self.assertNotIn('R1',a.spc_signals([3],0,1))
        self.assertNotIn('R2',a.spc_signals([2.2,0,-2.2],0,1))
        self.assertNotIn('R4',a.spc_signals([.1]*7+[0],0,1))
        self.assertNotIn('R5',a.spc_signals([0,.1,.2,.2,.3,.4],0,1))
        self.assertNotIn('R6',a.spc_signals([.1,-.1]*6+[0,0],0,1))
        self.assertEqual(a.spc_signals([1],1,0),[])
        self.assertEqual(a.spc_signals([2],1,0),['ZERO'])

    def test_priority_spec_first_then_sigma_rules_and_frequency(self):
        def item(name,kinds,z,rules=1,count=1):
            return dict(plant='test',sample=name,analyte='item',unit='%',sample_type='中间品',
                        kinds=kinds,severity=dict(max_sigma=z,spc_rule_count=rules),
                        abnormal_points=count,last_abnormal='2026-10-12')
        items=[item('spc extreme',['spc'],100),item('spec mild',['spec'],1),
               item('spec extreme',['spec','spc'],5),item('spc no sigma',['spc'],None),
               item('spc more rules',['spc'],4,3),item('spc more occurrences',['spc'],4,1,3),
               item('spc fewer occurrences',['spc'],4,1,1)]
        a.sort_items(items)
        self.assertEqual([s['sample'] for s in items],['spec extreme','spec mild','spc extreme',
                         'spc more rules','spc more occurrences','spc fewer occurrences','spc no sigma'])

    def test_severity_uses_worst_point_and_handles_zero_sigma(self):
        def point(day,value,sigma,code):
            return dict(sampled_at=day,value=value,baseline=dict(center=10,sigma=sigma),
                        reasons=[dict(kind='spc',code=code)])
        points=[point('2026-10-08',30,2,'R1'),point('2026-10-12',12,2,'R4')]
        result=a.severity(points)
        self.assertEqual(result['max_sigma'],10)
        self.assertEqual(result['point']['sampled_at'],'2026-10-08')
        self.assertEqual(result['spc_rule_count'],2)
        self.assertIsNone(a.severity([point('2026-10-12',12,0,'ZERO')])['max_sigma'])

    def test_spec_inclusive_and_no_fuzzy_or_wrong_unit(self):
        rule=next(r for r in a.specifications()['rules'] if a.matches(r,self.key))
        row=dict(self.key,sampled_at='2026-09-01',result_raw='4')
        self.assertEqual(a.check_spec(row,[rule])[0],[])
        self.assertEqual(len(a.check_spec(dict(row,result_raw='4.01'),[rule])[0]),1)
        self.assertTrue(a.check_spec(dict(row,result_raw='5',unit='mg/L'),[rule])[1])
        self.assertFalse(a.check_spec(dict(row,result_raw='5',sampled_at='2026-07-31'),[rule])[0])
        self.assertFalse(a.matches(rule,dict(self.key,sample='大槽焦油(3T-1401A)')))
        self.assertIsNone(a.factor('g/Nm3','mg/m3'))
        self.assertEqual(a.factor('g/m3','mg/m3'),1000)

    def test_qualitative_and_conditional_rules(self):
        key=dict(plant='针状焦二期',sample='轻相(2SC-409)',analyte='喹啉不溶物')
        rule=next(r for r in a.specifications()['rules'] if a.matches(r,key))
        row=dict(sampled_at='2026-09-01',result_raw='微量',unit='')
        self.assertEqual(a.check_spec(row,[rule]),([],[]))
        row.update(result_raw='少量',unit='%')
        self.assertTrue(a.check_spec(row,[rule])[0])
        self.assertFalse(a.check_spec(row,[rule])[1])
        rule=next(r for r in a.specifications()['rules'] if r.get('condition'))
        self.assertTrue(a.check_spec(dict(row,result_raw='100'),[rule])[1])

    def test_duplicates_keep_individual_spec_failures(self):
        rows=[dict(id=i,sampled_at='2026-09-01',result_raw=str(v),unit='%') for i,v in enumerate([1,5])]
        points,_=a.assess(rows,self.key)
        self.assertEqual(points[0]['value'],3)
        self.assertTrue(points[0]['abnormal'])
        self.assertEqual(points[0]['records'],2)
        self.assertIn('5',points[0]['reasons'][0]['message'])

    def test_baseline_excludes_target_and_converted_text(self):
        rows=[dict(id=i,sampled_at=(date(2026,8,1)+timedelta(days=i)).isoformat(),result_raw=str(1+i%2*.1),unit='%') for i in range(30)]
        rows.append(dict(id=31,sampled_at='2026-09-01',result_raw='痕迹',unit='%'))
        rows.append(dict(id=32,sampled_at='2026-09-02',result_raw='10',unit='%'))
        points,_=a.assess(rows,self.key)
        self.assertEqual(points[-1]['baseline']['count'],30)
        self.assertAlmostEqual(points[-1]['baseline']['center'],1.05)
        self.assertIn('R1',[r['code'] for r in points[-1]['reasons']])
        self.assertIsNone(points[-2]['value'])

    def test_recent_only_default_10_and_range_independent(self):
        for i in range(50):
            self.insert(date(2026,7,24)+timedelta(days=i),1+(i%2)*.1)
        self.insert('2026-09-04',5)  # Outside seven-day window.
        self.insert('2026-09-05',6)  # Inclusive first day.
        self.insert('2026-09-11',7)  # Inclusive final day.
        self.insert('2026-09-12',100)  # Future result excluded.
        today=date(2026,9,11)
        report=a.report({'days':['7']},today=today,db_factory=self.connect)
        self.assertEqual(report['window_start'],'2026-09-05')
        self.assertEqual(report['total'],1)
        item=report['items'][0]
        self.assertGreaterEqual(item['abnormal_points'],2)
        self.assertEqual(sum(any(r['kind']=='spec' for r in p['reasons']) for p in item['points'] if p['sampled_at'][:10]>='2026-09-05'),2)
        self.assertEqual(len(item['points']),10)
        self.assertNotIn('2026-09-12',[p['sampled_at'][:10] for p in item['points']])
        params={k:[v] for k,v in self.key.items()}
        default=a.trend(params,today=today,db_factory=self.connect)
        span=a.trend(dict(params,start=['2026-09-05'],end=['2026-09-11']),today=today,db_factory=self.connect)
        self.assertEqual(len(default['points']),10)
        self.assertEqual(default['points'][-1],span['points'][-1])
        stale=a.report({},today=date(2026,10,13),db_factory=self.connect)
        self.assertEqual(stale['items'],[])
        self.assertTrue(stale['stale'])

    def test_isolation_filters_and_invalid_range(self):
        self.insert('2026-09-10',10)
        self.insert('2026-09-10',10,unit='mg/L')
        result=a.report({'kind':['spec'],'days':['7']},today=date(2026,9,11),db_factory=self.connect)
        self.assertEqual(result['total'],1)
        self.assertEqual(result['checked_series'],2)
        self.assertEqual(a.report({'plant':["' OR 1=1 --"]},today=date(2026,9,11),db_factory=self.connect)['total'],0)
        with self.assertRaises(ValueError):
            a.trend({**{k:[v] for k,v in self.key.items()},'start':['2026-09-10']},db_factory=self.connect)
        with self.assertRaises(ValueError):
            a.trend({**{k:[v] for k,v in self.key.items()},'start':['2026-10-12'],'end':['2099-01-01']},db_factory=self.connect)

    def test_selectable_recent_days_and_validation(self):
        for day in [12,13,14,15]:
            self.insert(f'2026-10-{day}',5)
        today=date(2026,10,14)
        one=a.report({},today=today,db_factory=self.connect)
        two=a.report({'days':['2']},today=today,db_factory=self.connect)
        seven=a.report({'days':['7']},today=today,db_factory=self.connect)
        self.assertEqual(one['window_start'],'2026-10-14')
        self.assertEqual(two['window_start'],'2026-10-13')
        self.assertEqual(seven['window_start'],'2026-10-08')
        self.assertEqual([d['recent_records'] for d in [one,two,seven]],[1,2,3])
        self.assertEqual([d['items'][0]['abnormal_points'] for d in [one,two,seven]],[1,2,3])
        for val in ['0','91','1.5','abc']:
            with self.assertRaises(ValueError):
                a.report({'days':[val]},today=today,db_factory=self.connect)


if __name__=='__main__':
    unittest.main()
