import io
import json
import unittest
from urllib.error import HTTPError, URLError
from unittest.mock import Mock, patch

from safarai.advisor import request_json
from safarai.errors import ServiceError, ContextLimitError, describe_failure, provider_failure


class FailureTests(unittest.TestCase):
    def test_provider_failures_have_distinct_codes(self):
        cases = [(401, None, 'provider_authentication', 503, False),
                 (403, None, 'provider_access', 503, False),
                 (429, 'insufficient_quota', 'provider_quota', 503, False),
                 (429, 'rate_limit_exceeded', 'provider_rate_limit', 429, True),
                 (504, None, 'provider_timeout', 504, True),
                 (400, None, 'provider_request', 502, False),
                 (500, None, 'provider_unavailable', 503, True)]
        for status, reason, code, public_status, retryable in cases:
            failure = provider_failure(status, reason)
            self.assertEqual((failure.code, failure.status, failure.retryable), (code, public_status, retryable))

    def test_http_error_body_is_not_disclosed(self):
        body = io.BytesIO(json.dumps({'error': {'code': 'insufficient_quota', 'message': 'private-key-details'}}).encode())
        error = HTTPError('https://api.openai.com/v1/chat/completions', 429, 'private', {'x-request-id': 'req_123'}, body)
        opener = Mock()
        opener.open.side_effect = error
        with patch('safarai.advisor.build_opener', return_value=opener):
            with self.assertRaises(ServiceError) as caught:
                request_json('https://api.openai.com/v1/chat/completions')
        self.assertEqual(caught.exception.code, 'provider_quota')
        self.assertEqual(caught.exception.provider_request_id, 'req_123')
        self.assertNotIn('private', str(caught.exception))

    def test_network_timeout_is_distinct_from_connection_failure(self):
        for error, code in [(TimeoutError(), 'provider_timeout'), (URLError(TimeoutError()), 'provider_timeout'), (URLError('offline'), 'provider_connection')]:
            opener = Mock()
            opener.open.side_effect = error
            with patch('safarai.advisor.build_opener', return_value=opener), self.assertRaises(ServiceError) as caught:
                request_json('https://api.openai.com/v1/chat/completions')
            self.assertEqual(caught.exception.code, code)

    def test_internal_failures_are_not_misreported_as_quota(self):
        self.assertEqual(describe_failure(ContextLimitError('Too much detail')).code, 'context_limit')
        self.assertEqual(describe_failure(ValueError('private')).code, 'plan_validation')
        self.assertNotIn('private', str(describe_failure(ValueError('private'))))
