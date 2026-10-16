"""Offline tests for the advisor, benchmarks, and notebook controls."""
import base64
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from safarai.advisor import (
    MODEL_RATES, CONTEXT_BUDGET, TravelAdvisor, external_tool, request_json,
    safe_markdown, token_bound, validate_arguments,
)
from experiments.compare_models import compare
from safarai.errors import ServiceError


def answer(text=None, call=None, usage=None, finish_reason="stop"):
    return {"choices": [{"message": {"content": text, **({"tool_calls": [call]} if call else {})}, "finish_reason": finish_reason}],
            "usage": usage or {"prompt_tokens": 100, "completion_tokens": 20, "prompt_tokens_details": {"cached_tokens": 40}}}

def tool_call(name, **arguments):
    return {"id": "call_1", "type": "function", "function": {"name": name, "arguments": json.dumps(arguments)}}

class AdvisorTests(unittest.TestCase):
    def advisor(self, *responses, **kwargs):
        kwargs.setdefault('web_search', False)
        return TravelAdvisor("fake-key", transport=Mock(side_effect=responses), **kwargs)

    def test_handoff_uses_models_and_actual_constraints(self):
        advisor = self.advisor(answer(call=tool_call("generate_itinerary", destination="Berlin", days=2, interests=["museums"])), answer("# Berlin plan"))
        text = advisor.reply("Two days in Berlin, museums, wheelchair access, vegetarian food")
        calls = advisor.transport.call_args_list
        self.assertEqual([c.kwargs['payload']['model'] for c in calls], ['gpt-4o-mini', 'gpt-4.1-mini'])
        self.assertIn('wheelchair', json.dumps(calls[1].kwargs['payload']['messages']))
        self.assertEqual(text, advisor.last_itinerary)
        self.assertEqual(len(advisor.history), 2)
        self.assertAlmostEqual(advisor.cost_usd, .000084)

    def test_followup_does_not_generate(self):
        advisor = self.advisor(answer("Where would you like to go?"))
        advisor.reply("I like museums")
        self.assertIsNone(advisor.last_itinerary)
        self.assertEqual(advisor.transport.call_count, 1)

    def test_handoff_preserves_traveler_count(self):
        arguments = {'destination': 'Copenhagen', 'days': 2, 'interests': ['vegetarian food'], 'travelers': 2}
        self.assertEqual(validate_arguments('generate_itinerary', arguments)['travelers'], 2)
        for invalid in (True, 0, 21):
            with self.assertRaises(ValueError):
                validate_arguments('generate_itinerary', dict(arguments, travelers=invalid))

    def test_comparison_stops_on_auth_failure(self):
        transport = Mock(side_effect=ServiceError('provider_authentication', 'Invalid server credentials.', 503))
        with tempfile.TemporaryDirectory() as directory:
            rows = compare('fake', output_dir=directory, transport=transport)
        self.assertEqual(transport.call_count, 1)
        self.assertEqual(rows[0]['error_code'], 'provider_authentication')

    def test_invalid_budget_gets_one_automatic_repair(self):
        invalid = '### Day 1: Lisbon\nVisit a museum.\n## Estimated budget\nUnknown'
        repaired = '### Day 1: Lisbon\nVisit a museum.\n## Estimated budget\n```budget-json\n' + json.dumps({'currency': 'EUR', 'items': [{'id': 'food', 'category': 'Food', 'label': 'Meals', 'unit_cost': '40', 'quantity': '1'}]}) + '\n```'
        advisor = self.advisor(answer(call=tool_call('generate_itinerary', destination='Lisbon', days=1, interests=['food'])), answer(invalid), answer(repaired))
        result = advisor.reply('One day Lisbon, food')
        self.assertIn('**EUR 40.00**', result)
        self.assertNotIn('No total is shown', result)
        self.assertEqual(advisor.transport.call_count, 3)

    def test_failed_repairs_preserve_previous_plan(self):
        invalid = '### Day 1: Berlin\n## Trip summary\nFits EUR 220.\n## Estimated budget\nUnknown'
        advisor = self.advisor(answer(call=tool_call('generate_itinerary', destination='Berlin', days=2, interests=['museums'])),
                               answer(invalid), answer(invalid), answer(invalid))
        advisor.last_itinerary = 'Previously validated plan'
        advisor.last_brief = {'destination': 'Berlin', 'days': 2}
        with self.assertRaises(ServiceError) as caught:
            advisor.reply('Reduce the budget to EUR 220')
        self.assertEqual(caught.exception.code, 'plan_validation')
        self.assertEqual(advisor.last_itinerary, 'Previously validated plan')
        self.assertEqual(advisor.last_brief['destination'], 'Berlin')
        self.assertEqual(advisor.history, [])
        self.assertEqual(advisor.transport.call_count, 4)

    def test_kyoto_research_revision_fits_context_and_does_not_stack_drafts(self):
        ledger = {'currency': 'JPY', 'items': [{'id': 'food', 'category': 'Food', 'label': 'Meals', 'unit_cost': '3000', 'quantity': '4'}]}
        compact = '### Day 1: Kyoto\nVisit gardens.\n## Estimated budget\n```budget-json\n' + json.dumps(ledger) + '\n```'
        large_draft = compact.replace('Visit gardens.', 'Visit gardens and explore local culture. ' * 600)
        invalid = compact.replace('"quantity": "4"', '"quantity": "invalid"')
        advisor = self.advisor(answer(large_draft), answer(invalid), answer(compact), web_search=True)
        advisor.research_venues = Mock(return_value={'garden': {'name': 'Kyoto garden', 'address': 'Kyoto', 'evidence': 'Garden address',
            'source_url': 'https://example.org/garden', 'checked_at': '2026-10-16', 'status': 'web_sourced'}})
        result = advisor.planning_reply([{'role': 'user', 'content': 'Four days in Kyoto, gardens and food.'}],
                                        {'destination': 'Kyoto', 'days': 4, 'interests': ['gardens', 'food']})
        self.assertIn('**JPY 12000.00**', result)
        payloads = [call.kwargs['payload'] for call in advisor.transport.call_args_list]
        self.assertGreater(token_bound(payloads[1]['messages']), 24000)
        self.assertEqual(sum(m['role'] == 'assistant' for m in payloads[2]['messages']), 1)
        self.assertNotIn(large_draft, [m.get('content') for m in payloads[2]['messages']])
        self.assertIn('example.org/garden', payloads[2]['messages'][-1]['content'])

    def test_external_lookup_before_handoff(self):
        lookup = tool_call("get_exchange_rate", amount=200, from_currency="EUR", to_currency="USD")
        advisor = self.advisor(answer(call=lookup), answer(call=tool_call("generate_itinerary", destination="Berlin", days=2, interests=["museums"])), answer("# Berlin"),
                               fetch=Mock(return_value={"result": "success", "rates": {"USD": 1.1}, "time_last_update_utc": "today"}))
        advisor.reply("Berlin, 2 days, museums, convert EUR 200 to USD")
        planner_messages = advisor.transport.call_args_list[2].kwargs['payload']['messages']
        self.assertTrue(any(m['role'] == 'tool' and 'ExchangeRate-API' in m['content'] for m in planner_messages))
        self.assertEqual(advisor.metrics[-1]['purpose'], 'itinerary')
        self.assertIn('[ExchangeRate-API](https://open.er-api.com/v6/latest/EUR)', advisor.last_itinerary)
        self.assertIn('rate updated today', advisor.last_itinerary)

    def test_failed_lookup_is_data_not_fabrication(self):
        advisor = self.advisor(answer(call=tool_call("get_weather_forecast", city="Berlin")), answer("Weather lookup failed."), fetch=Mock(side_effect=RuntimeError("offline")))
        advisor.reply("Berlin weather?")
        messages = advisor.transport.call_args_list[-1].kwargs['payload']['messages']
        self.assertIn('Lookup failed', messages[-1]['content'])

    def test_malicious_arguments_never_fetch(self):
        invalid = [
            ('get_exchange_rate', {'amount': float('nan'), 'from_currency': 'EUR', 'to_currency': 'USD'}),
            ('get_exchange_rate', {'amount': True, 'from_currency': 'EUR', 'to_currency': 'USD'}),
            ('get_exchange_rate', {'amount': 1, 'from_currency': '../', 'to_currency': 'USD'}),
            ('get_weather_forecast', {'city': 'Berlin', 'url': 'http://localhost'}),
            ('generate_itinerary', {'destination': 'Berlin', 'days': True, 'interests': ['food']}),
            ('generate_itinerary', {'destination': 'Berlin', 'days': 2, 'interests': []}),
            ('run_shell', {'command': 'whoami'}),
        ]
        for name, args in invalid:
            with self.subTest(name=name, args=args), self.assertRaises(ValueError):
                validate_arguments(name, args)
        fetch = Mock()
        with self.assertRaises(ValueError):
            external_tool('run_shell', {}, fetch)
        fetch.assert_not_called()

    def test_unapproved_hosts_and_credential_forwarding(self):
        for url in ['http://api.openai.com/v1/', 'https://localhost/', 'https://api.openai.com.evil.test/', 'https://api.openai.com:1234/']:
            with self.assertRaises(ValueError):
                request_json(url)
        with self.assertRaises(ValueError):
            request_json('https://open.er-api.com/v6/latest/USD', api_key='secret')

    def test_safe_rendering(self):
        rendered = safe_markdown('# Hello\n\n<script>alert(1)</script>\n\n[bad](javascript:alert(1))\n\n![tracker](https://evil.test/a)\n\n| A | B |\n|---|---|\n|1|2|')
        self.assertNotIn('<script', rendered)
        self.assertNotIn('href="javascript:', rendered)
        self.assertNotIn('<img', rendered)
        self.assertIn('<table>', rendered)

    def test_failed_turn_usage_and_reset(self):
        advisor = self.advisor(answer(''))
        with self.assertRaises(ValueError):
            advisor.reply('hello')
        self.assertEqual(len(advisor.metrics), 1)
        self.assertFalse(advisor.history)
        advisor.reset()
        self.assertEqual(advisor.cost_usd, 0)
        self.assertFalse(advisor.metrics)
        self.assertFalse(advisor.summary)

    def test_context_and_budget_preflight(self):
        advisor = self.advisor()
        with self.assertRaises(ValueError):
            advisor.reply('a' * 6001)
        with self.assertRaises(ValueError):
            advisor.chat('gpt-4o-mini', [{'role': 'user', 'content': 'a' * CONTEXT_BUDGET}], 'test')
        advisor.budget_usd = 0
        with self.assertRaises(RuntimeError):
            advisor.reply('hello')
        advisor.transport.assert_not_called()

    def test_summarizes_long_history_and_preserves_recent_turn(self):
        advisor = self.advisor(*([answer('User wants Berlin, wheelchair access, vegetarian food; budget EUR 400.')] * 8))
        advisor.history = [{'role': role, 'content': ('wheelchair constraint ' * 300) if i < 6 else 'Latest correction: Berlin, not Paris.'}
                           for i, role in enumerate(['user', 'assistant'] * 4)]
        recent = advisor.history[-2:]
        advisor.compact({'role': 'user', 'content': 'Continue'})
        self.assertEqual(advisor.history, recent)
        self.assertIn('wheelchair', advisor.summary)
        self.assertEqual(advisor.summary_count, 1)
        self.assertLess(token_bound(advisor.conversation_messages()), 16000)
        for call in advisor.transport.call_args_list:
            self.assertEqual(call.kwargs['payload']['model'], 'gpt-4o-mini')

    def test_failed_summary_does_not_discard_history(self):
        advisor = self.advisor(RuntimeError('offline'))
        advisor.history = [{'role': 'user' if i % 2 == 0 else 'assistant', 'content': 'x' * 4000} for i in range(8)]
        history = list(advisor.history)
        with self.assertRaises(RuntimeError):
            advisor.compact({'role': 'user', 'content': 'continue'})
        self.assertEqual(advisor.history, history)
        self.assertEqual(advisor.summary, '')

    def test_image_cost_and_private_context(self):
        png = b'\x89PNG\r\n\x1a\nmock'
        advisor = self.advisor({'data': [{'b64_json': base64.b64encode(png).decode()}], 'usage': {'input_tokens': 100, 'output_tokens': 400}})
        advisor.history = [{'role': 'user', 'content': 'private dietary details'}]
        advisor.last_itinerary = '# Berlin'
        advisor.last_brief = {'destination': 'Berlin', 'days': 2, 'interests': ['museums']}
        self.assertEqual(advisor.generate_image(), png)
        self.assertNotIn('private dietary', advisor.transport.call_args.kwargs['payload']['prompt'])
        self.assertAlmostEqual(advisor.cost_usd, .00625)

    def test_image_failure_preserves_itinerary(self):
        advisor = self.advisor(RuntimeError('image failed'))
        advisor.last_itinerary = '# Berlin'
        advisor.last_brief = {'destination': 'Berlin', 'days': 2, 'interests': ['museums']}
        with self.assertRaises(RuntimeError):
            advisor.generate_image()
        self.assertEqual(advisor.last_itinerary, '# Berlin')

    def test_comparison_is_separate_and_writes_artifacts(self):
        transport = Mock(return_value=answer('# Example'))
        with tempfile.TemporaryDirectory() as directory:
            rows = compare('fake', output_dir=directory, transport=transport)
            self.assertEqual(len(rows), 9)
            self.assertTrue(all(row['success'] for row in rows))
            self.assertTrue(Path(directory, 'report.md').exists())
            self.assertTrue(Path(directory, 'review.csv').exists())

    def test_notebook_controls_restore_after_image_failure(self):
        advisor = self.advisor(answer(call=tool_call('generate_itinerary', destination='Lisbon', days=2, interests=['food'])), answer('# Lisbon'), RuntimeError('image unavailable'))
        namespace = {'advisor': advisor}
        with patch('IPython.display.display'):
            namespace.update(MODEL_RATES=MODEL_RATES, safe_markdown=safe_markdown)
            for cell in json.loads((Path(__file__).resolve().parents[1] / 'experiments/notebooks/notebook_ui.ipynb').read_text(encoding='utf-8'))['cells']:
                if cell['cell_type'] == 'code':
                    exec(''.join(cell['source']), namespace)
        namespace['display'] = Mock()
        namespace['message_box'].value = 'Two days in Lisbon, food'
        namespace['send_message'](None)
        self.assertTrue(advisor.last_itinerary.startswith('# Lisbon'))
        self.assertIn('not verified routes', advisor.last_itinerary)
        self.assertFalse(namespace['send_button'].disabled)
        self.assertFalse(namespace['reset_button'].disabled)
        self.assertEqual(namespace['message_box'].value, '')
        self.assertIn('Estimated cost', namespace['usage_display'].value)
    def test_new_trip_button_clears_state_and_visible_outputs(self):
        advisor = self.advisor()
        namespace = {'advisor': advisor, 'MODEL_RATES': MODEL_RATES, 'safe_markdown': safe_markdown}
        with patch('IPython.display.display'):
            for cell in json.loads((Path(__file__).resolve().parents[1] / 'experiments/notebooks/notebook_ui.ipynb').read_text(encoding='utf-8'))['cells']:
                if cell['cell_type'] == 'code':
                    exec(''.join(cell['source']), namespace)
        advisor.history = [{'role': 'user', 'content': 'Old trip'}]
        advisor.summary = 'Old summary'
        advisor.last_itinerary = '# Old itinerary'
        advisor.last_brief = {'destination': 'Berlin', 'days': 2, 'interests': ['food']}
        advisor.metrics = [{'cost_usd': 0.1}]
        namespace['show_markdown']('Old visible itinerary')
        namespace['chat_output'].append_display_data({'text/plain': 'Old image'})
        namespace['message_box'].value = 'Old draft'
        namespace['reset_button'].click()
        self.assertEqual(advisor.history, [])
        self.assertEqual(advisor.summary, '')
        self.assertIsNone(advisor.last_itinerary)
        self.assertIsNone(advisor.last_brief)
        self.assertEqual(advisor.cost_usd, 0)
        self.assertEqual(namespace['message_box'].value, '')
        outputs = namespace['chat_output'].outputs
        self.assertEqual(len(outputs), 1)
        self.assertIn('Hi! Where would you like to go', outputs[0]['data']['text/html'])
        self.assertIn('Started a new trip', namespace['status'].value)
        advisor.transport.assert_not_called()


if __name__ == "__main__":
    unittest.main()
