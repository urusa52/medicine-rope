import json
import unittest
from unittest.mock import patch

from studio_client import analyze_photo, build_job_payload


class LatestVersionTests(unittest.TestCase):
    def test_payload_omits_config_id(self):
        payload = build_job_payload('agt_test', 'file-test')
        self.assertNotIn('config_id', payload)
        self.assertEqual(payload['model'], 'agt_test')
        self.assertEqual(payload['input'][0]['content'][0]['file_id'], 'file-test')

    def test_each_analysis_creates_new_job(self):
        calls = []
        def request(path, key, payload=None, content_type='application/json', method=None):
            calls.append((path, payload))
            if path == '/files':
                return {'id': 'file-test'}
            if method == 'DELETE':
                return {'deleted': True}
            self.assertEqual(json.loads(payload)['config_id'], 'old-config-ignored')
            return {'id': 'job-test', 'status': 'completed', 'config_id': '8',
                    'output_text': '{"product_name":"test"}'}
        with patch('studio_client.settings', return_value={
                'UPSTAGE_API_KEY': 'test-only', 'UPSTAGE_AGENT_ID': 'agt_test',
                'UPSTAGE_CONFIG_ID': 'old-config-ignored'}), patch('studio_client._request', side_effect=request):
            for _ in range(2):
                result = analyze_photo(b'test-photo', 'image/png')
                self.assertEqual(result['config_policy'], 'pinned')
                self.assertEqual(result['config_id'], '8')
        self.assertEqual(sum(path == '/responses' for path, _ in calls), 2)


if __name__ == '__main__':
    unittest.main()
