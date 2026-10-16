import json
import unittest
from unittest.mock import Mock

from safarai.advisor import TravelAdvisor
from safarai.research import parse_research, research_query, safe_source, MAX_SEARCH_CALLS
from safarai.validation import structured_budget
from tests.test_advisor import answer, tool_call


def research_response(url='https://www.example.org/tickets', price='35.50'):
    fact = {'id': 'museum', 'name': 'Example Museum', 'address': 'Museum Street, Paris',
            'neighborhood': 'Central Paris', 'unit_price': price, 'currency': 'EUR',
            'price_scope': 'standard adult admission', 'source_url': url, 'evidence': 'Standard adult ticket: EUR 35.50; verify visit-date availability.'}
    return {'status': 'completed', 'usage': {'input_tokens': 100, 'output_tokens': 50}, 'output': [
        {'type': 'web_search_call', 'status': 'completed', 'action': {'type': 'search', 'sources': [{'url': url}]}},
        {'type': 'message', 'content': [{'type': 'output_text', 'text': json.dumps({'facts': [fact]}),
                                      'annotations': [{'type': 'url_citation', 'url': url}]}]}]}


def draft():
    ledger = {'currency': 'EUR', 'cap_per_person': '50', 'items': [
        {'id': 'museum_ticket', 'venue_id': 'museum', 'label': 'Example Museum entry', 'category': 'Activities', 'unit_cost': '10', 'quantity': '1'}]}
    return '### Day 1: Paris\nVisit Example Museum.\n## Estimated budget\n```budget-json\n' + json.dumps(ledger) + '\n```'


class WebResearchTests(unittest.TestCase):
    def test_transport_research_preserves_product_and_closure_status(self):
        response = research_response(price='28.50')
        content = response['output'][1]['content'][0]
        data = json.loads(content['text'])
        fact = data['facts'][0]
        fact.update(price_kind='transport', price_scope='standard adult AB 48h pass', price_option='Adult AB 48h', operating_status='unknown')
        content['text'] = json.dumps(data)
        parsed = parse_research(response)['museum']
        self.assertEqual(parsed['price_kind'], 'transport')
        self.assertEqual(parsed['unit_price'], '28.50')
        fact['price_option'] = None
        content['text'] = json.dumps(data)
        self.assertNotIn('unit_price', parse_research(response)['museum'])
        fact.update(operating_status='permanently_closed', unit_price=None)
        content['text'] = json.dumps(data)
        self.assertEqual(parse_research(response)['museum']['operating_status'], 'permanently_closed')

    def test_transport_targets_are_included_even_when_venues_fill_limit(self):
        ledger = {'research_venues': ['Museum ' + str(i) for i in range(6)],
                  'items': [{'label': 'WelcomeCard AB 48h', 'category': 'Transport'}]}
        query = research_query('```budget-json\n' + json.dumps(ledger) + '\n```', 'Berlin')
        self.assertIn('WelcomeCard AB 48h', query)
        self.assertIn('transport fares', query)
        self.assertIn('Berlin', research_query('```budget-json\ninvalid\n```', 'Berlin'))

    def test_sourced_facts_and_price_override_outside_lisbon(self):
        facts = parse_research(research_response())
        output = structured_budget(draft(), facts)
        self.assertIn('**EUR 35.50**', output)
        self.assertIn('web-sourced reference', output)
        self.assertIn('https://www.example.org/tickets', output)

    def test_hallucinated_url_and_absent_search_are_rejected(self):
        response = research_response()
        response['output'][0]['action']['sources'] = []
        response['output'][1]['content'][0]['annotations'] = []
        with self.assertRaises(ValueError):
            parse_research(response)
        response = research_response()
        response['output'].pop(0)
        with self.assertRaises(ValueError):
            parse_research(response)

    def test_unknown_price_is_not_promoted_to_verified_cost(self):
        for price in [None, 'NaN', '-5', 'Infinity']:
            facts = parse_research(research_response(price=price))
            self.assertNotIn('unit_price', facts['museum'])
            with self.assertRaises(ValueError):
                structured_budget(draft(), facts)

    def test_eligibility_specific_price_is_not_used_as_generic_admission(self):
        response = research_response()
        content = response['output'][1]['content'][0]
        data = json.loads(content['text'])
        data['facts'][0]['price_scope'] = 'standard adult admission for non-EEA residents'
        content['text'] = json.dumps(data)
        fact = parse_research(response)['museum']
        self.assertNotIn('unit_price', fact)
        self.assertIn('price_unconfirmed_reason', fact)

    def test_accented_source_identifier_is_normalized(self):
        response = research_response()
        content = response['output'][1]['content'][0]
        data = json.loads(content['text'])
        data['facts'][0]['id'] = 'mus\u00e9e_orsay'
        content['text'] = json.dumps(data)
        self.assertIn('musee_orsay', parse_research(response))

    def test_source_linked_item_without_display_id(self):
        text = draft().replace('"id": "museum_ticket", ', '')
        result = structured_budget(text, parse_research(research_response()))
        self.assertIn('**EUR 35.50**', result)

    def test_restaurant_location_does_not_establish_meal_price(self):
        response = research_response(price=None)
        facts = parse_research(response)
        text = draft().replace('"category": "Activities"', '"category": "Food"').replace('Example Museum entry', 'Vegetarian dinner')
        result = structured_budget(text, facts)
        self.assertIn('meal estimate; venue location web-sourced', result)
        self.assertIn('**EUR 10.00**', result)

    def test_tracking_parameters_do_not_break_provenance(self):
        response = research_response('https://www.example.org/tickets?utm_source=openai')
        content = response['output'][1]['content'][0]
        content['text'] = content['text'].replace('/tickets?utm_source=openai', '/tickets')
        self.assertIn('museum', parse_research(response))

    def test_cited_prose_is_extracted_without_another_search(self):
        response = research_response()
        original_json = response['output'][1]['content'][0]['text']
        response['output'][1]['content'][0]['text'] = 'Official adult entry costs EUR 35.50. https://www.example.org/tickets'
        transport = Mock(side_effect=[response, answer(original_json)])
        advisor = TravelAdvisor('fake', transport=transport, web_search=True)
        facts = advisor.research_venues(draft(), 'Paris')
        self.assertIn('museum', facts)
        self.assertEqual(transport.call_count, 2)
        self.assertTrue(transport.call_args.args[0].endswith('chat/completions'))
        self.assertEqual([m['purpose'] for m in advisor.metrics], ['web research', 'web fact extraction'])

    def test_no_unsafe_citation_links(self):
        for url in ['javascript:alert(1)', 'http://example.org', 'https://user:secret@example.org', 'https://example.org/a)bad']:
            self.assertFalse(safe_source(url))

    def test_research_is_capped_and_counted_in_usage(self):
        transport = Mock(return_value=research_response())
        advisor = TravelAdvisor('fake', transport=transport, web_search=True)
        facts = advisor.research_venues(draft(), 'Paris')
        self.assertIn('museum', facts)
        self.assertEqual(transport.call_args.args[0], 'https://api.openai.com/v1/responses')
        payload = transport.call_args.kwargs['payload']
        self.assertFalse(payload['store'])
        self.assertEqual(payload['max_tool_calls'], MAX_SEARCH_CALLS)
        self.assertGreater(advisor.cost_usd, .01)
        self.assertEqual(advisor.metrics[-1]['search_calls'], 1)
        self.assertIn('web-search fees', advisor.usage_summary_html())

    def test_budget_rejection_prevents_network_call(self):
        transport = Mock()
        advisor = TravelAdvisor('fake', transport=transport, web_search=True, budget_usd=.001)
        with self.assertRaises(RuntimeError):
            advisor.research_venues(draft(), 'Paris')
        transport.assert_not_called()

    def test_failed_request_reserves_cost_conservatively(self):
        advisor = TravelAdvisor('fake', transport=Mock(side_effect=RuntimeError('network')), web_search=True)
        with self.assertRaises(RuntimeError):
            advisor.research_venues(draft(), 'Paris')
        self.assertGreater(advisor.cost_usd, .03)
        self.assertTrue(advisor.metrics[-1]['usage_missing'])

    def test_end_to_end_search_revision_and_citations(self):
        transport = Mock(side_effect=[answer(call=tool_call('generate_itinerary', destination='Paris', days=1, interests=['museums'])),
                                     answer(draft()), research_response(), answer(draft())])
        advisor = TravelAdvisor('fake', transport=transport, web_search=True)
        result = advisor.reply('One day in Paris, museums, EUR 50 per person')
        self.assertIn('**EUR 35.50**', result)
        self.assertIn('## Web research sources', result)
        self.assertIn('Checked ', result)
        self.assertEqual(transport.call_count, 4)

    def test_failed_search_is_disclosed_without_blocking_estimated_plan(self):
        plan = '### Day 1: Paris\nExplore.\n## Estimated budget\n```budget-json\n' + json.dumps({'currency': 'EUR', 'items': [{'id': 'food', 'label': 'Meals', 'category': 'Food', 'unit_cost': '20', 'quantity': '1'}]}) + '\n```'
        transport = Mock(side_effect=[answer(call=tool_call('generate_itinerary', destination='Paris', days=1, interests=['food'])), answer(plan), RuntimeError('provider details')])
        advisor = TravelAdvisor('fake', transport=transport, web_search=True)
        result = advisor.reply('One day Paris food')
        self.assertIn('Web research unavailable', result)
        self.assertIn('**EUR 20.00**', result)
        self.assertNotIn('provider details', result)
