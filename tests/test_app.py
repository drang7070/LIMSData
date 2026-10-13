import base64
import os
import json
from pathlib import Path
import unittest
from unittest.mock import patch
from app import create_app


class WebTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(os.environ,{'LIMS_ENV':'production','LIMS_WEB_USERNAME':'test','LIMS_WEB_PASSWORD':'testing-only-password','LIMS_ENABLE_SYNC':'false'})
        self.env.start()
        self.client=create_app().test_client()
        self.auth={'Authorization':'Basic '+base64.b64encode(b'test:testing-only-password').decode()}

    def tearDown(self):
        self.env.stop()

    def test_authentication_and_health(self):
        self.assertEqual(self.client.get('/healthz').status_code,200)
        self.assertEqual(self.client.get('/api/lims/status').status_code,401)
        self.assertEqual(self.client.get('/').status_code,401)
        self.assertEqual(self.client.get('/',headers=self.auth).status_code,200)
        manifest=json.loads((Path(__file__).resolve().parents[1]/'seed/manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(self.client.get('/api/lims/status',headers=self.auth).json['coverage'][0]['records'],manifest['records'])

    def test_anomaly_routes_are_authenticated_and_validate_dates(self):
        for path in ['/lims/anomalies','/api/lims/anomalies','/api/lims/anomaly-trend','/api/lims/anomaly-rules']:
            self.assertEqual(self.client.get(path).status_code,401)
        response=self.client.get('/lims/anomalies',headers=self.auth)
        self.assertEqual(response.status_code,200)
        self.assertIn('近 7 天异常概览',response.get_data(as_text=True))
        self.assertEqual(len(self.client.get('/api/lims/anomaly-rules',headers=self.auth).json['rules']),91)
        self.assertEqual(self.client.get('/api/lims/anomaly-trend?start=bad',headers=self.auth).status_code,400)

    def test_real_example_and_stream_export(self):
        query={'plant':'三排送','sample':'3P-1402','analyte':'喹啉不溶物','start':'2026-09-01','end':'2026-09-11'}
        response=self.client.get('/api/lims/query',query_string=query,headers=self.auth)
        self.assertEqual(response.json['summary']['records'],1)
        self.assertEqual(response.json['rows'][0]['value'],1.53)
        export=self.client.get('/api/lims/export',query_string=query,headers=self.auth)
        self.assertIn('1.53',export.get_data(as_text=True))
        self.assertEqual(len(export.get_data(as_text=True).splitlines()),2)

    def test_sync_is_disabled_and_invalid_dates(self):
        self.assertEqual(self.client.post('/api/lims/sync',json={'days':1},headers=self.auth).status_code,403)
        self.assertEqual(self.client.get('/api/lims/query?start=bad',headers=self.auth).status_code,400)

    def test_production_requires_credentials(self):
        with patch.dict(os.environ,{'LIMS_WEB_PASSWORD':''}):
            with self.assertRaises(RuntimeError):
                create_app()
