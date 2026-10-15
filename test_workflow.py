import base64
import copy
import io
import json
import unittest
from unittest.mock import patch

import main
from workflow import AnalysisWorkflow
from mfds_client import ServiceError
from test_matcher import photo, detail


class FakeClient:
    def __init__(self, products=None, details=None):
        self.products = products if products is not None else [detail()['product']]
        self.details = details or {'123':detail()}
    def search(self,name,page=1,**kwargs):
        return {'items':self.products,'total':len(self.products),'source':'https://example.test'}
    def detail(self,code):
        return copy.deepcopy(self.details[code])


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.flow=AnalysisWorkflow(FakeClient(),lambda content,mime:{'extracted':photo(),'warnings':[]})

    def test_new_route_success(self):
        with patch('main.WORKFLOW',self.flow):
            result=main.dispatch_post('/api/analyze-and-match',{'base64':base64.b64encode(b'photo').decode(),'mime':'image/png','consent':True})
        self.assertEqual(len(result['candidates']),1)
        self.assertEqual(result['candidates'][0]['comparison']['status'],'일치')
        self.assertNotIn('selected_candidate',result)

    def test_no_result_keeps_extraction(self):
        self.flow.client=FakeClient([])
        result=self.flow.analyze_and_match(b'photo','image/png')
        self.assertEqual(result['status'],'제품을 찾지 못함')
        self.assertEqual(result['photo_extracted']['product_name_raw'],'가상 제품')
        self.assertTrue(result['photo_id'])

    def test_no_name(self):
        result=self.flow.match_extracted({'effective_ingredients':[]})
        self.assertEqual(result['status'],'제품을 찾지 못함')

    def test_search_error_keeps_extraction(self):
        with patch.object(self.flow.client,'search',side_effect=ServiceError('인증 오류')):
            result=self.flow.match_extracted(photo())
        self.assertEqual(result['status'],'확인 필요')
        self.assertEqual(result['photo_extracted'],photo())

    def test_ranking(self):
        d1=detail(code='101');d1['product']['ENTP_NAME']='다른제약'
        d2=detail(qnt='650',code='102')
        d3=detail(code='103')
        self.flow.client=FakeClient([d1['product'],d2['product'],d3['product']],{'101':d1,'102':d2,'103':d3})
        result=self.flow.match_extracted(photo())
        self.assertEqual([c['product']['ITEM_SEQ'] for c in result['candidates']],['103','102','101'])

    def test_explicit_confirmation_required(self):
        result=self.flow.match_extracted(photo());candidate=result['candidates'][0]['candidate_id']
        with self.assertRaises(ServiceError):
            self.flow.confirm_candidate(candidate,False)
        confirmed=self.flow.confirm_candidate(candidate,True)
        self.assertTrue(confirmed['approval_id'])

    def test_mismatch_needs_second_ack(self):
        result=self.flow.match_extracted(photo('650mg'));candidate=result['candidates'][0]['candidate_id']
        with self.assertRaises(ServiceError):
            self.flow.confirm_candidate(candidate,True)
        result=self.flow.confirm_candidate(candidate,True,True)
        self.assertEqual(result['product']['ingredients'][0]['amount'],'500')

    def test_unknown_official_cannot_override(self):
        self.flow.client=FakeClient(details={'123':detail(unit='001')})
        candidate=self.flow.match_extracted(photo())['candidates'][0]['candidate_id']
        with self.assertRaises(ServiceError):
            self.flow.confirm_candidate(candidate,True,True)

    def test_client_tampering_ignored(self):
        candidate=self.flow.match_extracted(photo())['candidates'][0]['candidate_id']
        approval=self.flow.confirm_candidate(candidate,True)
        result=self.flow.evaluate([{'approval_id':approval['approval_id'],'quantity':'1',
                                   'ingredients':[{'amount':'999999'}],'name':'改変'}],'single',[])
        self.assertEqual(result['ingredients'][0]['total_mg'],'500')

    def test_no_approval_no_calculation(self):
        with self.assertRaises(ServiceError):
            self.flow.evaluate([{'quantity':'1','ingredients':[]}],'single',[])

    def test_manual_search_preserves_photo(self):
        first=self.flow.match_extracted(photo('650mg'))
        result=self.flow.search_again(first['photo_id'],'재검색')
        self.assertEqual(result['candidates'][0]['comparison']['rows'][0]['status'],'함량 불일치')

    def test_ttl(self):
        first=self.flow.match_extracted(photo())
        with patch('workflow.time.monotonic',return_value=10**15):
            with self.assertRaises(ServiceError):
                self.flow.confirm_candidate(first['candidates'][0]['candidate_id'],True)

    def test_route_rejects_no_consent(self):
        with self.assertRaises(ServiceError):
            main.dispatch_post('/api/analyze-and-match',{'base64':'eA==','consent':False})

    def test_route_rejects_bad_base64(self):
        with self.assertRaises(ServiceError):
            main.dispatch_post('/api/analyze-and-match',{'base64':'%%','consent':True})

    def test_http_route_delegates(self):
        handler=object.__new__(main.Handler)
        raw=json.dumps({'base64':'eA==','mime':'image/png','consent':True}).encode()
        handler.headers={'Host':'127.0.0.1:8767','Origin':'http://127.0.0.1:8767',
                         'X-App-Token':main.TOKEN,'Content-Length':str(len(raw))}
        handler.path='/api/analyze-and-match';handler.rfile=io.BytesIO(raw)
        sent=[];handler.send=lambda status,payload:sent.append((status,payload))
        with patch('main.ALLOWED_HOST','127.0.0.1:8767'),patch('main.WORKFLOW',self.flow):
            handler.do_POST()
        self.assertEqual(sent[0][0],200)
        self.assertEqual(sent[0][1]['candidates'][0]['comparison']['status'],'일치')
