import json
import unittest

from safarai.validation import structured_budget, remove_weather_claims, qualify_routes, validate_plan_format, validation_reason


class ValidationTests(unittest.TestCase):
    def test_budget_failures_have_safe_specific_diagnostics(self):
        from decimal import InvalidOperation
        cases = [
            (InvalidOperation('private-model-output'), 'invalid_amount'),
            (TypeError('private-model-output'), 'field_type'),
            (ValueError('Invalid budget cap.'), 'budget_cap'),
            (ValueError('Each group cost needs unit_basis=per_person or per_group.'), 'cost_basis'),
            (ValueError('Each non-meal cost needs a positive integer occurrences count.'), 'cost_occurrences'),
            (ValueError('Invalid cost or quantity.'), 'invalid_amount'),
        ]
        for error, expected in cases:
            code, message = validation_reason(error)
            self.assertEqual(code, expected)
            self.assertNotIn('private-model-output', message)

    def test_missing_duplicate_and_out_of_order_days_are_rejected(self):
        ledger = '\n```budget-json\n{}\n```'
        for numbers in ([1], [1, 1], [2, 1], [1, 3]):
            text = '\n'.join(f'### Day {day}: Exploring' for day in numbers) + ledger
            with self.assertRaisesRegex(ValueError, 'day count'):
                validate_plan_format(text, expected_days=2)
        validate_plan_format('### Day 1: Exploring\n### Day 2: Food' + ledger, expected_days=2)

    def test_weather_filter_keeps_calculated_budget_rows(self):
        plan = self.plan([self.item('Rain cover', '10'), self.item('Weather contingency', '5')])
        result = structured_budget(plan, {})
        cleaned = remove_weather_claims(result + '\nWeather: guaranteed sunshine.')
        self.assertIn('| Weather contingency | EUR 5.00', cleaned)
        self.assertIn('**EUR 15.00**', cleaned)
        self.assertNotIn('guaranteed sunshine', cleaned)

    def plan(self, items, cap=None, narrative='### Day 1: Exploring\nTaxi EUR 50.'):
        return narrative + '\n## Estimated budget\n```budget-json\n' + json.dumps({'currency': 'EUR', 'cap_per_person': cap, 'items': items}) + '\n```'

    def item(self, identifier, cost, category='Food', **extra):
        return {'id': identifier, 'label': identifier, 'category': category, 'unit_cost': cost, 'quantity': '1', **extra}

    def facts(self):
        return {'museum': {'name': 'Museum', 'status': 'web_sourced', 'unit_price': '18', 'currency': 'EUR',
                           'source_url': 'https://example.org/tickets', 'checked_at': '2026-10-16'}}

    def test_reported_arithmetic_regression(self):
        result = structured_budget(self.plan([self.item('Food', '40'), self.item('Lodging', '100'), self.item('Transport', '40'), self.item('Activities', '20')]), {})
        self.assertIn('**EUR 200.00**', result)
        self.assertNotIn('Taxi EUR 50', result)

    def test_decimal_arithmetic_and_shared_costs(self):
        result = structured_budget(self.plan([self.item('Meal', '0.10'), self.item('Snack', '0.20'), self.item('Taxi', '20', quantity='0.5')]), {})
        self.assertIn('**EUR 10.30**', result)

    def test_invalid_costs_and_duplicate_ids_are_rejected(self):
        for items in [[self.item('Food', '1,200')], [self.item('Food', 'NaN')], [self.item('Food', '-1')],
                      [self.item('Food', '20', quantity='0')], [self.item('Food', '20'), self.item('Food', '30')], [None]]:
            with self.assertRaises((ValueError, ArithmeticError)):
                structured_budget(self.plan(items), {})

    def test_source_prices_override_estimates_and_expose_cap_overrun(self):
        facts = self.facts()
        facts['museum']['unit_price'] = '33'
        items = [self.item('Museum', '16', 'Activities', venue_id='museum'), self.item('Other costs', '243')]
        result = structured_budget(self.plan(items, '250'), facts)
        self.assertIn('**EUR 276.00**', result)
        self.assertIn('Over cap by EUR 26.00', result)
        self.assertIn('https://example.org/tickets', result)

    def test_transport_contingency_is_explicit(self):
        result = structured_budget(self.plan([self.item('Outbound taxi', '30', 'Transport'), self.item('Return taxi', '20', 'Transport'),
                                             self.item('Transport contingency', '20', 'Transport')]), {})
        self.assertIn('| Transport | EUR 70.00 |', result)
        self.assertIn('Transport contingency', result)

    def test_source_id_without_price_cannot_price_admission(self):
        with self.assertRaises(ValueError):
            structured_budget(self.plan([self.item('Museum entry', '10', 'Activities', venue_id='museum')]), {})

    def test_researched_admission_cannot_be_free_or_omitted(self):
        result = structured_budget(self.plan([self.item('Museum entry', '0', 'Activities')]), self.facts())
        self.assertIn('**EUR 18.00**', result)
        with self.assertRaises(ValueError):
            structured_budget(self.plan([self.item('Meals', '40')], narrative='### Day 1: Museum\nVisit Museum.'), self.facts())
        exterior = structured_budget(self.plan([self.item('Museum exterior only', '0', 'Activities')]), {})
        self.assertIn('**EUR 0.00**', exterior)

    def test_unsupported_weather_prose_removed(self):
        result = remove_weather_claims('## Tips\nNovember 6-7 rain is under 10%.\nVisit a museum.')
        self.assertNotIn('under 10%', result)
        self.assertIn('Visit a museum', result)
        self.assertIn('No prediction', result)

    def test_route_warning_is_clear_and_not_duplicated(self):
        result = qualify_routes(qualify_routes('A five-minute walk to a nearby restaurant.'))
        self.assertNotIn('five-minute', result)
        self.assertNotIn('nearby', result)
        self.assertNotIn('location to verify', result)
        self.assertEqual(result.count('**Route and access check:**'), 1)
