import json
import unittest
from html.parser import HTMLParser

from safarai.validation import structured_budget
from safarai.advisor import safe_markdown
from safarai.research import parse_research
from tests.test_web_research import research_response


class GroupBudgetTests(unittest.TestCase):
    def test_rendered_budget_keeps_conclusions_outside_tables(self):
        class TableParser(HTMLParser):
            def __init__(self):
                super().__init__()
                self.in_table = False
                self.table_text = []
                self.row_cells = []
            def handle_starttag(self, tag, attrs):
                if tag == 'table': self.in_table = True
                if tag == 'tr': self.row_cells.append(0)
                if tag in ('td', 'th'): self.row_cells[-1] += 1
            def handle_endtag(self, tag):
                if tag == 'table': self.in_table = False
            def handle_data(self, data):
                if self.in_table: self.table_text.append(data)
        result = safe_markdown(structured_budget(self.plan(self.data()), {}))
        parser = TableParser()
        parser.feed(result)
        self.assertEqual(parser.row_cells, [5] * 7 + [2] * 7)
        self.assertNotIn('Group cap:', ''.join(parser.table_text))
        self.assertNotIn('Only listed items', ''.join(parser.table_text))
        self.assertIn('<p>Estimated group total', result)

    def data(self):
        return {'currency': 'DKK', 'cost_basis': 'group', 'travelers': 2, 'days': 2,
                'start_date': '2026-11-06', 'cap': {'amount': '1800', 'basis': 'group'},
                'items': [
                    {'id': 'breakfast', 'label': 'Packed breakfasts', 'category': 'Food', 'unit_cost': '0', 'unit_basis': 'per_person', 'meal': 'breakfast', 'days': [1, 2]},
                    {'id': 'lunch', 'label': 'Packed lunches', 'category': 'Food', 'unit_cost': '0', 'unit_basis': 'per_person', 'meal': 'lunch', 'days': [1, 2]},
                    {'id': 'dinner', 'label': 'Vegetarian dinners', 'category': 'Food', 'unit_cost': '100', 'unit_basis': 'per_person', 'meal': 'dinner', 'days': [1, 2]},
                    {'id': 'museum', 'label': 'Museum tickets', 'category': 'Activities', 'unit_cost': '150', 'unit_basis': 'per_person', 'occurrences': 1},
                    {'id': 'transport', 'label': 'Two-day transport passes', 'category': 'Transport', 'unit_cost': '100', 'unit_basis': 'per_person', 'occurrences': 1},
                    {'id': 'hotel', 'label': 'Shared hotel room', 'category': 'Lodging', 'unit_cost': '1060', 'unit_basis': 'per_group', 'occurrences': 1}]}

    def plan(self, data):
        return '# Copenhagen\n## Trip summary\nThis plan fits the budget perfectly.\n## Day-by-day itinerary\n### Day 1: Museums\nExplore the city.\n## Estimated budget\n```budget-json\n' + json.dumps(data) + '\n```'

    def test_copenhagen_totals_and_dinners(self):
        result = structured_budget(self.plan(self.data()), {}, trip={'destination': 'Copenhagen', 'travelers': 2, 'days': 2, 'interests': ['vegetarian food']})
        self.assertIn('| Vegetarian dinners | DKK 100.00 | 4 | DKK 400.00 |', result)
        self.assertIn('| Museum tickets | DKK 150.00 | 2 | DKK 300.00 |', result)
        self.assertIn('| **Total group (2 travelers)** | **DKK 1960.00** |', result)
        self.assertIn('| **Total per person** | **DKK 980.00** |', result)
        self.assertIn('Over cap by DKK 160.00', result)
        summary = result.split('## Trip summary')[1].split('## Day-by-day itinerary')[0]
        self.assertNotIn('fits the budget perfectly', summary)
        self.assertIn('DKK 1960.00', summary)
        self.assertIn('does not meet the cap', summary)

    def test_missing_or_duplicate_dinner_is_rejected(self):
        for days in ([1], [1, 1]):
            data = self.data()
            data['items'][2]['days'] = days
            with self.assertRaises(ValueError):
                structured_budget(self.plan(data), {})
        data = self.data()
        data['items'].append(dict(data['items'][2], id='duplicate'))
        with self.assertRaises(ValueError):
            structured_budget(self.plan(data), {})

    def test_cairo_admissions_use_visits_while_meals_use_days(self):
        data = self.data()
        data.update(currency='EGP', cap=None)
        data['items'] = data['items'][:3] + [
            dict(id='giza', label='Giza admission', category='Activities', kind='admission', unit_cost='700', unit_basis='per_person', visit_days=[1], occurrences=2),
            dict(id='museum', label='Egyptian Museum admission', category='Activities', kind='admission', unit_cost='550', unit_basis='per_person', visit_days=[2], occurrences=2),
            dict(id='transport', label='Shared transport', category='Transport', unit_cost='1080', unit_basis='per_group', occurrences=1)]
        result = structured_budget(self.plan(data), {})
        self.assertIn('| Giza admission | EGP 700.00 | 2 | EGP 1400.00 |', result)
        self.assertIn('| Egyptian Museum admission | EGP 550.00 | 2 | EGP 1100.00 |', result)
        self.assertIn('| Vegetarian dinners | EGP 100.00 | 4 | EGP 400.00 |', result)
        self.assertIn('**EGP 3980.00**', result)
        self.assertIn('**EGP 1990.00**', result)

    def test_repeated_admissions_require_explicit_valid_visit_days(self):
        data = self.data()
        admission = data['items'][3]
        admission['occurrences'] = 2
        with self.assertRaisesRegex(ValueError, 'actual visits'):
            structured_budget(self.plan(data), {})
        for visits in ([1, 1], [3], [True], []):
            admission['visit_days'] = visits
            with self.assertRaises(ValueError):
                structured_budget(self.plan(data), {})
        admission['visit_days'] = [1, 2]
        self.assertIn('| Museum tickets | DKK 150.00 | 4 | DKK 600.00 |', structured_budget(self.plan(data), {}))

    def test_admission_price_sentence_is_readable(self):
        text = self.plan(self.data()).replace('Explore the city.', 'Museum admission is DKK 150 per person.')
        result = structured_budget(text, {})
        self.assertNotIn('is see cost ledger', result)
        self.assertIn('Admission costs are listed in the budget below.', result)

    def test_transport_uses_exact_sourced_product_and_price(self):
        data = self.data()
        transport = data['items'][4]
        transport.update(label='WelcomeCard AB 48h', venue_id='pass', unit_cost='26.80', price_option='Adult AB 48h')
        fact = dict(name='WelcomeCard AB 48h', status='web_sourced', currency='DKK', unit_price='28.50',
                    price_kind='transport', price_option='Adult AB 48h', source_url='https://example.org/fares', checked_at='2026-10-16')
        self.assertIn('| WelcomeCard AB 48h | DKK 28.50 | 2 | DKK 57.00 |', structured_budget(self.plan(data), {'pass': fact}))
        transport['price_option'] = 'Adult ABC 48h'
        with self.assertRaisesRegex(ValueError, 'exact sourced fare'):
            structured_budget(self.plan(data), {'pass': fact})

    def test_closed_restaurant_is_rejected_even_without_ledger_reference(self):
        text = self.plan(self.data()).replace('Explore the city.', 'Dinner at Kopps.')
        with self.assertRaisesRegex(ValueError, 'permanently closed'):
            structured_budget(text, {'kopps': {'name': 'Kopps', 'operating_status': 'permanently_closed'}})

    def test_city_only_rejects_sourced_neighboring_municipality(self):
        text = self.plan(self.data()).replace('Explore the city.', 'Visit Museo della Regina.')
        with self.assertRaisesRegex(ValueError, 'city-only'):
            structured_budget(text, {'museum': {'name': 'Museo della Regina', 'city': 'Cattolica'}},
                              trip={'destination': 'Riccione', 'city_only': True, 'travelers': 2, 'days': 2})

    def test_official_free_entry_overrides_estimated_admission(self):
        data = self.data()
        data['items'][3]['venue_id'] = 'museum'
        fact = dict(name='Museum', unit_price='0', currency='DKK', status='web_sourced', price_kind='admission',
                    source_url='https://example.org/museum', checked_at='2026-10-16')
        self.assertIn('| Museum tickets | DKK 0.00 | 2 | DKK 0.00 |', structured_budget(self.plan(data), {'museum': fact}))

    def test_model_exchange_rate_is_removed_before_cost_replacement(self):
        text = self.plan(self.data()).replace('Explore the city.', 'Exchange rate is EUR 1 = DKK 7.46.\nExplore the city.')
        result = structured_budget(text, {})
        self.assertNotIn('Exchange rate is', result)
        self.assertIn('Explore the city.', result)

    def test_trip_party_size_must_match_ledger(self):
        with self.assertRaises(ValueError):
            structured_budget(self.plan(self.data()), {}, trip={'travelers': 3, 'days': 2})

    def test_per_person_cap_is_converted_to_group_cap(self):
        data = self.data()
        data['cap'] = {'amount': '1000', 'basis': 'per_person'}
        result = structured_budget(self.plan(data), {})
        self.assertIn('Group cap: DKK 2000.00', result)
        self.assertIn('Remaining buffer: DKK 40.00', result)

    def test_sourced_individual_ticket_cannot_be_charged_once_per_group(self):
        data = self.data()
        data['items'][3].update(venue_id='museum', unit_basis='per_group')
        facts = {'museum': {'name': 'Museum', 'status': 'web_sourced', 'currency': 'DKK', 'unit_price': '150',
                            'source_url': 'https://example.org/ticket', 'checked_at': '2026-10-16'}}
        with self.assertRaises(ValueError):
            structured_budget(self.plan(data), facts)

    def test_menu_price_option_and_dates(self):
        data = self.data()
        dinner = data['items'][2]
        dinner.update(venue_id='hoest', price_option='Vegetarian three-course menu')
        fact = {'name': 'Hoest', 'status': 'web_sourced', 'currency': 'DKK', 'unit_price': '450', 'price_kind': 'meal',
                'price_option': 'Vegetarian three-course menu', 'source_url': 'https://cofoco.dk/en/restaurant/hoest',
                'checked_at': '2026-10-16', 'valid_from': '2026-09-24', 'valid_until': '2026-11-09'}
        result = structured_budget(self.plan(data), {'hoest': fact})
        self.assertIn('| Vegetarian dinners | DKK 450.00 | 4 | DKK 1800.00 |', result)
        dinner['price_option'] = 'Five-course menu'
        with self.assertRaises(ValueError):
            structured_budget(self.plan(data), {'hoest': fact})
        dinner['price_option'] = fact['price_option']
        data['start_date'] = '2026-11-09'
        with self.assertRaises(ValueError):
            structured_budget(self.plan(data), {'hoest': fact})

    def test_explicit_meal_exclusion_is_visible(self):
        data = self.data()
        data['items'].pop(0)
        data['meal_exclusions'] = [{'meal': 'breakfast', 'days': [1, 2], 'reason': 'Included in the booked hotel'}]
        self.assertIn('Day 2 breakfast: Included in the booked hotel', structured_budget(self.plan(data), {}))

    def test_menu_research_preserves_exact_option(self):
        response = research_response(price='450')
        content = response['output'][1]['content'][0]
        payload = json.loads(content['text'])
        payload['facts'][0].update(price_kind='meal', price_option='Vegetarian three-course menu', price_scope='vegetarian three-course menu',
                                   valid_from='2026-09-24', valid_until='2026-11-09', currency='DKK')
        content['text'] = json.dumps(payload)
        fact = parse_research(response)['museum']
        self.assertEqual(fact['unit_price'], '450')
        self.assertEqual(fact['price_kind'], 'meal')
        self.assertEqual(fact['valid_until'], '2026-11-09')
