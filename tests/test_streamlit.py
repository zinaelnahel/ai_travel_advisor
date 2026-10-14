import os
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from streamlit.testing.v1 import AppTest

from advisor import TravelAdvisor
from pdf_export import itinerary_pdf


class StreamlitTests(unittest.TestCase):
    def test_chat_itinerary_pdf_and_reset(self):
        advisor = TravelAdvisor('fake-key', transport=Mock())
        advisor.reply = Mock()

        def reply(text):
            advisor.last_itinerary = '# Lisbon\n## Day 1: Explore\nEnjoy the city.'
            advisor.last_brief = {'destination': 'Lisbon', 'days': 1, 'interests': ['food']}
            return advisor.last_itinerary

        advisor.reply.side_effect = reply
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-key', 'APP_PASSWORD': ''}), \
             patch('advisor.TravelAdvisor', return_value=advisor):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')).run(timeout=15)
            self.assertFalse(app.exception)
            app.chat_input[0].set_value('Lisbon, one day, food').run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(app.session_state['plan'], advisor.last_itinerary)
            self.assertEqual(len(app.session_state['chat']), 2)
            advisor.transport.assert_not_called()
            app.button[0].click().run(timeout=15)
            self.assertFalse(app.exception)
            self.assertIsNone(app.session_state['plan'])
            self.assertEqual(app.session_state['chat'], [])

    def test_pdf_contains_a_valid_document(self):
        plan = '# Cafe trip\n## Day 1\n- **Morning:** Cafes & museums.\n\n| Category | Cost |\n|---|---|\n| Food | EUR 50 |'
        data = itinerary_pdf(plan)
        self.assertTrue(data.startswith(b'%PDF-'))
        self.assertIn(b'%%EOF', data)

    def test_password_gate(self):
        with patch.dict(os.environ, {'OPENAI_API_KEY': 'fake-key', 'APP_PASSWORD': 'private'}):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / 'streamlit_app.py')).run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_input), 0)
            app.text_input[0].set_value('private')
            app.button[0].click().run(timeout=15)
            self.assertFalse(app.exception)
            self.assertEqual(len(app.chat_input), 1)


if __name__ == '__main__':
    unittest.main()
