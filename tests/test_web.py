import os
import unittest
from unittest.mock import Mock, patch

from web.app import TravelAdvisor, create_app


def answer(text=None, call=None):
    message = {'content': text}
    if call:
        message['tool_calls'] = [call]
    return {'choices': [{'message': message, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 50}}


class WebTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {'APP_PASSWORD': '', 'COOKIE_SECURE': 'false'})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.advisors = []

        def factory():
            advisor = TravelAdvisor('fake-key', transport=Mock(), itinerary_prompt='Plan a trip.')
            self.advisors.append(advisor)
            return advisor

        self.app = create_app(factory)
        self.client = self.app.test_client()
        self.headers = {'X-Travel-Request': '1'}

    def post(self, action, body=None, client=None):
        return (client or self.client).post('/api/' + action, json=body or {}, headers=self.headers)

    def test_home_and_static_assets(self):
        response = self.client.get('/')
        self.assertEqual(response.status_code, 200)
        self.assertIn(b'SafarAI', response.data)
        self.assertIn("frame-ancestors 'none'", response.headers['Content-Security-Policy'])
        for path in ['/static/style.css', '/static/app.js', '/health']:
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            response.close()

    def test_conversation_isolation_and_sanitized_reply(self):
        self.post('reset')
        self.advisors[0].transport.return_value = answer('<script>alert(1)</script> Where to?')
        response = self.post('chat', {'message': 'Hello'})
        self.assertEqual(response.status_code, 200)
        self.assertNotIn('<script>', response.json['reply'])
        other = self.app.test_client()
        self.post('reset', client=other)
        self.assertEqual(len(self.advisors[0].history), 2)
        self.assertEqual(self.advisors[1].history, [])

    def test_itinerary_handoff_manual_image_and_reset(self):
        self.post('reset')
        advisor = self.advisors[0]
        call = {'id': 'call_1', 'type': 'function', 'function': {'name': 'generate_itinerary',
                'arguments': '{"destination":"Lisbon","days":3,"interests":["food"]}'}}
        advisor.transport.side_effect = [answer(call=call), answer('# Lisbon\n\nA lovely trip.')]
        response = self.post('chat', {'message': 'Three days in Lisbon, food'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json['destination'], 'Lisbon')
        self.assertIn('<h1>', response.json['itinerary_html'])
        self.assertEqual(advisor.transport.call_count, 2)  # no automatic image charge
        advisor.generate_image = Mock(return_value=b'fake-png')
        first = self.post('image')
        self.assertEqual(first.status_code, 200)
        self.assertEqual(self.post('image').json['image'], first.json['image'])
        advisor.generate_image.assert_called_once()
        advisor.transport.side_effect = None
        advisor.transport.return_value = answer('A currency lookup can help.')
        followup = self.post('chat', {'message': 'Can you help with currency?'})
        self.assertIsNone(followup.json['itinerary'])
        self.assertEqual(self.post('image').json['image'], first.json['image'])
        advisor.generate_image.assert_called_once()
        response = self.post('reset')
        self.assertTrue(response.json['reset'])
        self.assertEqual(advisor.history, [])
        self.assertIsNone(advisor.last_itinerary)
        self.assertEqual(advisor.cost_usd, 0)
        self.assertEqual(self.post('image').status_code, 400)

    def test_invalid_requests_and_cross_site_protection(self):
        self.assertEqual(self.client.post('/api/chat', json={'message': 'Hi'}).status_code, 403)
        response = self.client.post('/api/reset', headers={**self.headers, 'Origin': 'https://evil.example'})
        self.assertEqual(response.status_code, 403)
        for body in [{'message': ''}, {'message': 123}, {'message': 'a' * 6001}, ['invalid']]:
            self.assertEqual(self.post('chat', body).status_code, 400)

    def test_direct_itinerary_is_routed_to_the_plan_panel(self):
        self.post('reset')
        advisor = self.advisors[0]
        call = {'id': 'call_1', 'type': 'function', 'function': {
            'name': 'generate_itinerary',
            'arguments': '{"destination":"Lisbon","days":2,"interests":["food"]}'}}
        for draft in ['# Lisbon\n## Day 1: Food\n## Day 2: Sights',
                      '# Lisbon\n**Day 1: Food**\nEstimated budget: EUR 100']:
            with self.subTest(draft=draft):
                advisor.transport.reset_mock()
                advisor.transport.side_effect = [answer(draft), answer(call=call), answer('# Final Lisbon plan')]
                response = self.post('chat', {'message': 'Lisbon, food, two days'})
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json['itinerary'], '# Final Lisbon plan')
                forced = advisor.transport.call_args_list[1].kwargs['payload']['tool_choice']
                self.assertEqual(forced['function']['name'], 'generate_itinerary')
                self.assertEqual(advisor.last_brief['destination'], 'Lisbon')
                self.assertEqual(len(advisor.history[-2:]), 2)

    def test_provider_failure_preserves_previous_plan(self):
        self.post('reset')
        advisor = self.advisors[0]
        advisor.last_itinerary = '# Lisbon'
        advisor.last_brief = {'destination': 'Lisbon', 'days': 3, 'interests': ['food']}
        advisor.generate_image = Mock(side_effect=RuntimeError('secret provider response'))
        response = self.post('image')
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('secret', response.json['error'])
        self.assertEqual(advisor.last_itinerary, '# Lisbon')

    def test_password_gate(self):
        with patch.dict(os.environ, {'APP_PASSWORD': 'test-password'}):
            self.assertEqual(self.client.get('/').status_code, 401)
            self.assertEqual(self.client.get('/health').status_code, 200)
            self.assertEqual(self.client.get('/', auth=('traveler', 'test-password')).status_code, 200)

    def test_https_proxy_accepts_same_origin_and_blocks_foreign_origin(self):
        with patch.dict(os.environ, {'TRUST_PROXY': 'true'}):
            app = create_app(lambda: TravelAdvisor('fake-key'))
        client = app.test_client()
        headers = {**self.headers, 'X-Forwarded-Proto': 'https', 'Origin': 'https://localhost'}
        self.assertEqual(client.post('/api/reset', json={}, headers=headers).status_code, 200)
        headers['Origin'] = 'https://evil.example'
        self.assertEqual(client.post('/api/reset', json={}, headers=headers).status_code, 403)


if __name__ == '__main__':
    unittest.main()
