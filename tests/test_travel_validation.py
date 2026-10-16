import unittest
from travel_validation import calculated_budget, remove_weather_claims, qualify_routes


class ValidationTests(unittest.TestCase):
    def budget(self, costs):
        return '# Lisbon\n\n## Estimated budget\n\n| Category | Estimated cost |\n|---|---|\n' + costs

    def test_reported_arithmetic_regression(self):
        result = calculated_budget(self.budget('| Food | EUR 40 |\n| Lodging | EUR 100 |\n| Transport | EUR 40 |\n| Activities | EUR 20 |\n| Total | EUR 250 |\nGroup total EUR 250, per person EUR 100'))
        self.assertIn('**EUR 200.00**', result)
        self.assertNotIn('250', result)
        self.assertNotIn('EUR 100\n', result)

    def test_ranges_and_decimal_sum(self):
        result = calculated_budget(self.budget('| Food | EUR 0.10-0.20 |\n| Lodging | EUR 0.20-0.40 |'))
        self.assertIn('**EUR 0.30-0.60**', result)

    def test_ambiguous_or_mixed_costs_hide_total(self):
        for costs in ['| Food | EUR 1,200 |', '| Food | EUR 20 |\n| Lodging | USD 40 |', '| Food | EUR 20 per day |', '| Food | EUR 20 |\n| Food | EUR 30 |']:
            self.assertIn('No total is shown', calculated_budget(self.budget(costs)))

    def test_unsupported_weather_prose_removed(self):
        result = remove_weather_claims('## Tips\nNovember 6-7 rain is under 10%.\nVisit a museum.')
        self.assertNotIn('under 10%', result)
        self.assertIn('Visit a museum', result)
        self.assertIn('No prediction', result)

    def test_route_claim_is_qualified(self):
        result = qualify_routes('A five-minute walk to a nearby restaurant.')
        self.assertNotIn('five-minute', result)
        self.assertNotIn('nearby', result)
        self.assertIn('not verified routes', result)
