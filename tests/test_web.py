import os
import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient
from itsdangerous import URLSafeTimedSerializer
from web.app import TravelAdvisor, create_app


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
            advisor = TravelAdvisor('fake-key', transport=self.transport, itinerary_prompt='Plan a trip.')
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

    def test_conversation_survives_new_app_instance_without_cookies(self):
        first = self.post('chat', message='Hello').json()
        self.client = TestClient(create_app(self.factory))
        second = self.post('chat', message='Lisbon please', state=first['state'])
        self.assertEqual(second.status_code, 200)
        self.assertEqual(len(self.advisors[-1].history), 4)
        self.assertFalse(second.cookies)
        self.post('chat', message='Unrelated browser')
        self.assertEqual(len(self.advisors[-1].history), 2)

    def test_tampered_and_expired_state(self):
        state = self.post('chat', message='Hi').json()['state']
        self.assertEqual(self.post('chat', message='Hi', state=state + 'tampered').status_code, 400)
        with patch('itsdangerous.timed.time.time', return_value=1):
            expired = self.post('reset').json()['state']
        self.assertEqual(self.post('chat', message='Hi', state=expired).status_code, 400)

    def test_itinerary_and_followup_image_across_instances(self):
        call = {'id': 'call_1', 'type': 'function', 'function': {'name': 'generate_itinerary',
                'arguments': '{"destination":"Lisbon","days":3,"interests":["food"]}'}}
        self.transport.side_effect = [
            {'choices': [{'message': {'content': None, 'tool_calls': [call]}, 'finish_reason': 'stop'}]},
            answer('# Lisbon\nA lovely trip.')]
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


if __name__ == '__main__':
    unittest.main()
