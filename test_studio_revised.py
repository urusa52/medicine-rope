import json
import unittest
from unittest.mock import patch
from studio_client import analyze_photo, build_job_payload, select_output
from mfds_client import ServiceError


def step(model,text):
    return {'model':model,'content':[{'type':'output_text','text':text}]}


class StudioRevisedTests(unittest.TestCase):
    def test_all_steps_requested(self):
        self.assertEqual(build_job_payload('agt_test','file-test')['include'],['all'])

    def test_config_pin(self):
        self.assertEqual(build_job_payload('agt_test','file-test',' 5 ')['config_id'],'5')
        self.assertNotIn('config_id',build_job_payload('agt_test','file-test','  '))

    def test_extract_before_instruct(self):
        result=select_output({'output':[step('step_parse','{}'),step('step_Extract','{"product_name_raw":"Extract"}'),step('instruct','{"product_name_raw":"Instruct"}')]})
        self.assertEqual(result[1]['product_name_raw'],'Extract')

    def test_final_fallback(self):
        result=select_output({'output':[step('parse','{}'),step('instruct','{"product_name_raw":"last"}')]})
        self.assertEqual(result[1]['product_name_raw'],'last')

    def test_bad_extract_warns(self):
        result=select_output({'output':[step('extract','not json'),step('instruct','{}')]})
        self.assertTrue(result[3])

    def test_null_output_defensive(self):
        result=select_output({'output':[None,{'model':'extract','content':None}]})
        self.assertIsNone(result[1])

    def test_delete_success_and_latest(self):
        calls=[]
        def request(path,key,payload=None,content_type=None,method=None):
            calls.append((path,method))
            if path=='/files':return {'id':'file-test'}
            if method=='DELETE':return {'deleted':True}
            return {'id':'job-test','status':'completed','output':[step('extract','{}')]}
        with patch('studio_client.settings',return_value={'UPSTAGE_API_KEY':'test','UPSTAGE_AGENT_ID':'agt_test'}),patch('studio_client._request',side_effect=request):
            result=analyze_photo(b'photo','image/png')
        self.assertIn(('/files/file-test','DELETE'),calls)
        self.assertEqual(result['file_cleanup'],'deleted')
        self.assertEqual(result['config_policy'],'latest')

    def test_delete_on_failed_job(self):
        methods=[]
        def request(path,key,payload=None,content_type=None,method=None):
            methods.append(method)
            if path=='/files':return {'id':'file-test'}
            if method=='DELETE':return {'deleted':True}
            return {'id':'job-test','status':'failed'}
        with patch('studio_client.settings',return_value={'UPSTAGE_API_KEY':'test','UPSTAGE_AGENT_ID':'agt_test'}),patch('studio_client._request',side_effect=request):
            with self.assertRaises(ServiceError):analyze_photo(b'photo','image/png')
        self.assertIn('DELETE',methods)

    def test_delete_on_create_exception(self):
        deleted=[]
        def request(path,key,payload=None,content_type=None,method=None):
            if path=='/files':return {'id':'file-test'}
            if method=='DELETE':deleted.append(True);return {'deleted':True}
            raise ServiceError('작업 생성 실패')
        with patch('studio_client.settings',return_value={'UPSTAGE_API_KEY':'test','UPSTAGE_AGENT_ID':'agt_test'}),patch('studio_client._request',side_effect=request):
            with self.assertRaisesRegex(ServiceError,'작업 생성 실패'):analyze_photo(b'photo','image/png')
        self.assertEqual(deleted,[True])

    def test_delete_failure_is_warning_and_redacted(self):
        def request(path,key,payload=None,content_type=None,method=None):
            if path=='/files':return {'id':'file-test'}
            if method=='DELETE':raise RuntimeError('secret-token-must-not-leak')
            return {'id':'job-test','status':'completed','output':[step('extract','{}')]}
        with patch('studio_client.settings',return_value={'UPSTAGE_API_KEY':'secret-token-must-not-leak','UPSTAGE_AGENT_ID':'agt_test'}),patch('studio_client._request',side_effect=request),self.assertLogs('studio_client',level='WARNING') as logs:
            result=analyze_photo(b'photo','image/png')
        self.assertEqual(result['file_cleanup'],'warning')
        self.assertNotIn('secret-token-must-not-leak',str(result)+str(logs.output))

    def test_polling_all_and_cleanup(self):
        paths=[]
        def request(path,key,payload=None,content_type=None,method=None):
            paths.append(path)
            if path=='/files':return {'id':'file-test'}
            if method=='DELETE':return {'deleted':True}
            if path=='/responses':return {'id':'job-test','status':'queued'}
            return {'id':'job-test','status':'completed','output':[step('extract','{}')]}
        with patch('studio_client.settings',return_value={'UPSTAGE_API_KEY':'test','UPSTAGE_AGENT_ID':'agt_test'}),patch('studio_client._request',side_effect=request),patch('studio_client.time.sleep'):
            analyze_photo(b'photo','image/png')
        self.assertIn('/responses/job-test?include=all',paths)


if __name__=='__main__':unittest.main()
