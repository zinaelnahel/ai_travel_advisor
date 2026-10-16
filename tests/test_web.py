import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from web.app import TravelAdvisor, create_app
from safarai.errors import ServiceError, ContextLimitError
from tests.test_advisor import valid_plan


def answer(text):
    return {'choices': [{'message': {'content': text}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}


class WebTests(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {'APP_PASSWORD': '', 'STATE_SIGNING_SECRET': 'test-secret-' * 4})
        env.start()
        self.addCleanup(env.stop)
        self.advisors = []
        self.transport = Mock(return_value=answer('Where to?'))

        def factory():
            advisor = TravelAdvisor('fake-key', transport=self.transport, itinerary_prompt='Plan a trip.', web_search=False)
            self.advisors.append(advisor)
            return advisor
        self.factory = factory
        self.client = TestClient(create_app(factory))
        self.headers = {'X-Travel-Request': '1'}

    def post(self, action, **body):
        return self.client.post('/api/' + action, json=body, headers=self.headers)

    def test_home_static_health(self):
        for path in ['/', '/static/style.css', '/static/app.js', '/health']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])

    def test_misconfigured_allowance_returns_safe_configuration_error(self):
        client = TestClient(create_app())
        for allowance in ('NaN', 'Infinity', '-1', 'not-a-number'):
            with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake', 'CONVERSATION_BUDGET_USD': allowance}):
                response = client.post('/api/chat', json={'message': 'Hello'}, headers=self.headers)
                self.assertEqual(response.status_code, 503)
                self.assertIn('misconfigured', response.json()['error'])

    def test_home_keeps_simple_chat_and_itinerary(self):
        page = self.client.get('/').text
        self.assertNotIn('id="lookup-city"', page)
        self.assertIn('id="chat-form"', page)
        self.assertIn('id="itinerary"', page)
        self.assertNotIn('id="trip-form"', page)
        self.assertNotIn('id="trip-start"', page)

    def test_conversation_survives_new_app_instance_without_cookies(self):
        first = self.post('chat', message='Hello').json()
        self.client = TestClient(create_app(self.factory))
        second = self.post('chat', message='Lisbon please', state=first['state'])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.advisors[-1].history), 4)
        self.assertFalse(second.cookies)
        self.post('chat', message='Unrelated browser')
        self.assertEqual(len(self.advisors[-1].history), 2)

    def test_swagger_schema_policy_and_authentication(self):
        docs = self.client.get('/docs')
        self.assertEqual(docs.status_code, 200)
        self.assertIn('/static/swagger.js', docs.text)
        self.assertIn('https://cdn.jsdelivr.net', docs.headers['Content-Security-Policy'])
        self.assertNotIn('https://cdn.jsdelivr.net', self.client.get('/').headers['Content-Security-Policy'])
        self.assertEqual(self.client.get('/static/swagger.js').status_code, 200)
        operation = self.client.get('/openapi.json').json()['paths']['/api/{action}']['post']
        self.assertIn('application/json', operation['requestBody']['content'])
        self.assertTrue(any(p['name'] == 'x-travel-request' for p in operation['parameters']))
        with patch.dict(os.environ, {'APP_PASSWORD': 'password'}):
            for path in ['/docs', '/openapi.json', '/static/swagger.js']:
                self.assertEqual(self.client.get(path).status_code, 401)
                self.assertEqual(self.client.get(path, auth=('user', 'password')).status_code, 200)

    def test_tampered_and_expired_state(self):
        state = self.post('chat', message='Hi').json()['state']
        self.assertEqual(self.post('chat', message='Hi', state=state + 'tampered').status_code, 400)
        with patch('itsdangerous.timed.time.time', return_value=1):
            expired = self.post('reset').json()['state']
        self.assertEqual(self.post('chat', message='Hi', state=expired).status_code, 400)

    def test_restore_signed_trip_without_model_call(self):
        first = self.post('chat', message='Hello').json()
        count = self.transport.call_count
        with patch.dict(os.environ, {'OPENAI_API_KEY': ''}):
            restored = self.post('restore', state=first['state'])
        self.assertEqual(restored.status_code, 200)
        self.assertEqual(restored.json()['messages'][0]['role'], 'user')
        self.assertEqual(self.transport.call_count, count)
        self.assertEqual(self.post('restore', state=first['state'] + 'tamper').status_code, 400)

    def test_json_revision_failure_preserves_plan_after_refresh(self):
        from tests.test_advisor import answer as model_answer, tool_call
        first = self.post('chat', message='Hello').json()
        signer = URLSafeTimedSerializer(os.environ['STATE_SIGNING_SECRET'], salt='safarai-state-v1')
        baseline = signer.loads(first['state'])
        baseline['last_itinerary'] = valid_plan('Riccione')
        baseline['last_brief'] = {'destination': 'Riccione', 'days': 2, 'interests': ['food']}
        self.transport.side_effect = [model_answer(call=tool_call('generate_itinerary', destination='Riccione', days=2, interests=['food'])),
                                      answer('{"budget":220}'), answer('{"budget":220}'), answer('{"budget":220}')]
        failed = self.post('chat', message='Riccione-only please', state=signer.dumps(baseline))
        self.assertEqual(failed.status_code, 502)
        self.assertTrue(failed.json()['has_itinerary'])
        restored = self.post('restore', state=failed.json()['state']).json()
        self.assertEqual(restored['itinerary'], baseline['last_itinerary'])

    def test_restore_does_not_duplicate_full_itinerary_in_chat(self):
        first = self.post('chat', message='Hello').json()
        signer = URLSafeTimedSerializer(os.environ['STATE_SIGNING_SECRET'], salt='safarai-state-v1')
        state = signer.loads(first['state'])
        state['history'].append({'role': 'assistant', 'content': '# Lisbon\n### Day 1: Museums\nFull itinerary details'})
        response = self.post('restore', state=signer.dumps(state)).json()
        self.assertNotIn('Full itinerary details', response['messages'][-1]['html'])
        self.assertIn('adventure plan is ready', response['messages'][-1]['html'])

    def test_itinerary_and_followup_image_across_instances(self):
        call = {'id': 'call_1', 'type': 'function', 'function': {'name': 'generate_itinerary',
                'arguments': '{"destination":"Lisbon","days":3,"interests":["food"]}'}}
        self.transport.side_effect = [
            {'choices': [{'message': {'content': None, 'tool_calls': [call]}, 'finish_reason': 'stop'}]},
            answer(valid_plan('Lisbon', days=3))]
        plan = self.post('chat', message='Lisbon, 3 days, food').json()
        self.assertEqual(plan['destination'], 'Lisbon')
        self.assertIn('<h1>', plan['itinerary_html'])
        self.transport.side_effect = None
        self.transport.return_value = answer('Happy to help!')
        followup = self.post('chat', message='Thanks', state=plan['state']).json()
        self.assertIsNone(followup['itinerary'])
        with patch.object(TravelAdvisor, 'generate_image', return_value=b'png'):
            image = self.post('image', state=followup['state'])
        self.assertEqual(image.status_code, 200)
        self.assertTrue(image.json()['image'].startswith('data:image/png;base64,'))
        reset = self.post('reset', state=image.json()['state']).json()
        self.assertEqual(self.post('image', state=reset['state']).status_code, 400)

    def test_input_and_origin_validation(self):
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Hi'}).status_code, 403)
        self.assertEqual(self.client.post('/api/reset', json={}, headers={**self.headers, 'Origin': 'https://evil.example'}).status_code, 403)
        self.assertEqual(self.client.post('/api/reset', json={}, headers={**self.headers, 'Origin': 'http://testserver'}).status_code, 200)
        for message in ['', 123, 'a' * 6001]:
            self.assertEqual(self.post('chat', message=message).status_code, 400)
        self.assertEqual(self.client.post('/api/chat', json=[], headers=self.headers).status_code, 400)
        self.assertEqual(self.client.post('/api/chat', content=b'x' * 1_000_001, headers=self.headers).status_code, 413)

    def test_password_and_missing_signing_secret(self):
        with patch.dict(os.environ, {'APP_PASSWORD': 'password'}):
            self.assertEqual(self.client.get('/').status_code, 401)
            self.assertEqual(self.client.get('/health').status_code, 200)
            self.assertEqual(self.client.get('/', auth=('user', 'password')).status_code, 200)
        with patch.dict(os.environ, {'STATE_SIGNING_SECRET': ''}):
            self.assertEqual(self.post('reset').status_code, 503)

    def test_provider_failure_does_not_disclose_secrets(self):
        self.transport.side_effect = RuntimeError('secret provider details')
        response = self.post('chat', message='Hello')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.json()['error'])

    def test_failure_codes_request_reference_and_safe_logs(self):
        for error, code, status in [(ServiceError('provider_quota', 'Provider billing allowance exhausted.', 503), 'provider_quota', 503),
                                    (ContextLimitError('Too much planning detail.'), 'context_limit', 422),
                                    (TimeoutError('private-provider-details'), 'provider_timeout', 504),
                                    (ValueError('private-validation-details'), 'plan_validation', 502)]:
            self.transport.side_effect = error
            with self.assertLogs('safarai.requests', level='ERROR') as captured:
                response = self.post('chat', message='private-trip-description')
            data = response.json()
            self.assertEqual(response.status_code, status)
            self.assertEqual(data['code'], code)
            self.assertEqual(data['request_id'], response.headers['X-Request-ID'])
            self.assertFalse(data['has_itinerary'])
            self.assertNotIn('itinerary is still available', data['error'])
            self.assertNotIn('private', '\n'.join(captured.output))
            self.assertIn(data['request_id'], captured.output[0])
            self.assertEqual(self.post('restore', state=data['state']).status_code, 200)

    def test_failed_revision_rolls_back_plan_but_keeps_usage(self):
        first = self.post('chat', message='Hello').json()
        signer = URLSafeTimedSerializer(os.environ['STATE_SIGNING_SECRET'], salt='safarai-state-v1')
        baseline = signer.loads(first['state'])
        baseline['last_itinerary'], baseline['last_brief'] = '# Previous Kyoto plan', {'destination': 'Kyoto'}
        def fail(advisor, text):
            advisor.last_itinerary = None
            advisor.history.append({'role': 'user', 'content': 'partial failed turn'})
            advisor.metrics.append({'model': 'gpt-4.1-mini', 'purpose': 'itinerary', 'input': 10, 'output': 10, 'cached_input': 0, 'cost_usd': .01, 'latency_s': 1, 'usage_missing': False})
            raise ServiceError('provider_timeout', 'Planning timed out.', 504, True)
        with patch.object(TravelAdvisor, 'reply', fail), self.assertLogs('safarai.requests', level='ERROR'):
            response = self.post('chat', message='Change my trip', state=signer.dumps(baseline))
        data = response.json()
        restored = signer.loads(data['state'])
        self.assertEqual(restored['history'], baseline['history'])
        self.assertEqual(restored['last_itinerary'], baseline['last_itinerary'])
        self.assertEqual(len(restored['metrics']), len(baseline['metrics'])+1)
        self.assertTrue(data['has_itinerary'])
        self.assertIn('previous itinerary is still available', data['error'])


if __name__ == '__main__':
    unittest.main()
