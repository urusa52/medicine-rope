import io
import unittest
from urllib.error import HTTPError
from unittest.mock import patch

from mfds_client import MFDSClient, ServiceError, gateway_error


class GatewayTests(unittest.TestCase):
    def test_registered_key_error_is_specific_and_redacted(self):
        raw = b'<OpenAPI_ServiceResponse><cmmMsgHeader><returnReasonCode>30</returnReasonCode><returnAuthMsg>secret-key-must-not-leak</returnAuthMsg></cmmMsgHeader></OpenAPI_ServiceResponse>'
        def transport(req, timeout):
            raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(raw))
        with patch('mfds_client.settings', return_value={'MFDS_SERVICE_KEY': 'secret-key-must-not-leak'}):
            with self.assertRaises(ServiceError) as raised:
                MFDSClient(transport).search('test')
        self.assertIn('오류 30', str(raised.exception))
        self.assertNotIn('secret-key-must-not-leak', str(raised.exception))
        self.assertNotIn('serviceKey', str(raised.exception))

    def test_plain_forbidden_not_assumed_quota(self):
        def transport(req, timeout):
            raise HTTPError(req.full_url, 403, 'Forbidden', {}, io.BytesIO(b'Forbidden'))
        with patch('mfds_client.settings', return_value={'MFDS_SERVICE_KEY': 'test'}):
            with self.assertRaises(ServiceError) as raised:
                MFDSClient(transport).search('test')
        self.assertIn('단정할 수 없습니다', str(raised.exception))

    def test_json_quota(self):
        reason = gateway_error(b'{"header":{"resultCode":"22","resultMsg":"secret"}}')
        self.assertIn('일일 호출 한도', reason)
        self.assertNotIn('secret', reason)

    def test_wrong_provider_key(self):
        with patch('mfds_client.settings', return_value={'MFDS_SERVICE_KEY': 'up_test'}):
            with self.assertRaisesRegex(ServiceError, 'Upstage'):
                MFDSClient().search('test')


if __name__ == '__main__':
    unittest.main()
