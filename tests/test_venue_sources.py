import json
import unittest
from unittest.mock import Mock

from venue_sources import venue_facts, MONUMENTS, RESTAURANT, fetch_official
from travel_validation import structured_budget, qualify_routes


class VenueTests(unittest.TestCase):
    def facts(self):
        pages = {MONUMENTS: 'MOSTEIRO DOS JERONIMOS BILHETE: 18 EUR TORRE DE BELEM BILHETE: 15 EUR', RESTAURANT: 'Beco do Jasmim, 18 (Mouraria, Lisbon)'}
        return venue_facts('Jeronimos and Belem Tower and The Food Temple', fetch=pages.__getitem__)

    def plan(self, items):
        return '### Day 1: Belem\nTaxi EUR 50.\n## Estimated budget\n```budget-json\n' + json.dumps({'currency': 'EUR', 'cap_per_person': '250', 'items': items}) + '\n```'

    def test_official_facts_and_one_fetch_per_page(self):
        fetch = Mock(return_value='MOSTEIRO DOS JERONIMOS BILHETE: 18 EUR TORRE DE BELEM BILHETE: 15 EUR')
        facts = venue_facts('Jeronimos and Belem Tower', fetch)
        self.assertEqual(fetch.call_count, 1)
        self.assertEqual(facts['jeronimos']['adult_entry_eur'], '18')
        self.assertEqual(facts['belem_tower']['adult_entry_eur'], '15')
        self.assertEqual(self.facts()['food_temple']['neighborhood'], 'Mouraria')

    def test_changed_page_and_network_failure_do_not_reuse_price(self):
        for fetch in [Mock(return_value='Changed page'), Mock(side_effect=OSError('offline'))]:
            facts = venue_facts('Jeronimos', fetch)
            self.assertEqual(facts['jeronimos']['status'], 'unavailable')
            self.assertNotIn('adult_entry_eur', facts['jeronimos'])
        with self.assertRaises(ValueError):
            fetch_official('https://localhost/')

    def test_official_prices_override_old_ledger_and_cap(self):
        items = [{'id': 'j', 'label': 'Jeronimos admission', 'category': 'Activities', 'unit_cost': '10', 'quantity': '1', 'venue_id': 'jeronimos'},
                 {'id': 'b', 'label': 'Belem Tower admission', 'category': 'Activities', 'unit_cost': '6', 'quantity': '1', 'venue_id': 'belem_tower'},
                 {'id': 'other', 'label': 'Other trip costs', 'category': 'Other', 'unit_cost': '243', 'quantity': '1'}]
        rendered = structured_budget(self.plan(items), self.facts())
        self.assertIn('**EUR 276.00**', rendered)
        self.assertIn('Over cap by EUR 26.00', rendered)
        self.assertIn('Official admission sources', rendered)
        self.assertNotIn('Taxi EUR 50', rendered)

    def test_transport_allowance_is_an_explicit_item(self):
        items = [{'id': 'taxi1', 'label': 'Outbound taxi share', 'category': 'Transport', 'unit_cost': '30', 'quantity': '1'},
                 {'id': 'taxi2', 'label': 'Return taxi share', 'category': 'Transport', 'unit_cost': '20', 'quantity': '1'},
                 {'id': 'buffer', 'label': 'Transport contingency', 'category': 'Transport', 'unit_cost': '20', 'quantity': '1'}]
        result = structured_budget(self.plan(items), {})
        self.assertIn('| Transport | EUR 70.00 |', result)
        self.assertIn('Transport contingency', result)

    def test_failed_official_price_rejects_paid_visit(self):
        with self.assertRaises(ValueError):
            structured_budget(self.plan([{'id': 'j', 'label': 'Jeronimos ticket', 'category': 'Activities', 'unit_cost': '10', 'quantity': '1'}]), {})

    def test_zero_cost_cannot_bypass_official_admission(self):
        item = {'id': 'j', 'label': 'Jeronimos ticket', 'category': 'Activities', 'unit_cost': '0', 'quantity': '1'}
        result = structured_budget(self.plan([item]), self.facts())
        self.assertIn('**EUR 18.00**', result)
        item['label'] = 'Jeronimos exterior only'
        result = structured_budget(self.plan([item]), {})
        self.assertIn('**EUR 0.00**', result)

    def test_unlisted_admission_is_rejected(self):
        plan = self.plan([{'id': 'food', 'label': 'Meals', 'category': 'Food', 'unit_cost': '40', 'quantity': '1'}])
        plan = plan.replace('Taxi EUR 50.', 'Visit Jeronimos Monastery.')
        with self.assertRaises(ValueError):
            structured_budget(plan, self.facts())

    def test_route_warning_is_idempotent(self):
        result = qualify_routes(qualify_routes('Choose a nearby restaurant.'))
        self.assertEqual(result.count('**Route and access check:**'), 1)
        self.assertNotIn('location to verify', result)
