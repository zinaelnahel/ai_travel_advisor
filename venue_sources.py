"""Small explicit official-source registry, not a general venue search service."""
import re
import unicodedata
from datetime import datetime, timezone
from html.parser import HTMLParser
from urllib.request import Request, build_opener, HTTPRedirectHandler

MONUMENTS = 'https://mosteirojeronimos.torrebelem.gov.pt/visitar'
RESTAURANT = 'https://www.thefoodtemple.com/'
CATALOG = {
    'jeronimos': {'name': 'Jeronimos Monastery', 'aliases': ['jeronimos'], 'url': MONUMENTS, 'heading': 'MOSTEIRO DOS JERONIMOS'},
    'belem_tower': {'name': 'Belem Tower', 'aliases': ['belem tower', 'torre de belem'], 'url': MONUMENTS, 'heading': 'TORRE DE BELEM'},
    'food_temple': {'name': 'The Food Temple', 'aliases': ['food temple'], 'url': RESTAURANT},
}


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()


class TextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts = []
        self.hidden = 0

    def handle_starttag(self, tag, attrs):
        if tag in ('script', 'style'):
            self.hidden += 1

    def handle_endtag(self, tag):
        if tag in ('script', 'style'):
            self.hidden = max(0, self.hidden - 1)

    def handle_data(self, data):
        if not self.hidden:
            self.parts.append(data.strip())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args):
        raise ValueError('Source redirects require review.')


def fetch_official(url):
    if url not in (MONUMENTS, RESTAURANT):
        raise ValueError('Unknown official source.')
    with build_opener(NoRedirect()).open(Request(url, headers={'User-Agent': 'SafarAI/3.0'}), timeout=8) as response:
        data = response.read(1_000_001)
    if len(data) > 1_000_000:
        raise ValueError('Source page is too large.')
    parser = TextParser()
    parser.feed(data.decode('utf-8'))
    return ' '.join(parser.parts)


def venue_facts(text, fetch=fetch_official):
    """Extract facts only when the known page structure matches. No price fallback."""
    requested = [key for key, entry in CATALOG.items() if any(alias in normalized(text) for alias in entry['aliases'])]
    pages, facts = {}, {}
    for key in requested:
        entry = CATALOG[key]
        record = {'name': entry['name'], 'source_url': entry['url'], 'checked_at': datetime.now(timezone.utc).isoformat(), 'status': 'unavailable'}
        try:
            if entry['url'] not in pages:
                pages[entry['url']] = fetch(entry['url'])
            page = normalized(pages[entry['url']])
            if key == 'food_temple':
                address = re.search(r'beco do jasmim\s*,?\s*(\d+)\s*\(mouraria,\s*lisbon\)', page)
                if address:
                    record.update(status='verified', neighborhood='Mouraria', address=f'Beco do Jasmim, {address[1]}, Mouraria, Lisbon')
            else:
                prices = re.findall(re.escape(normalized(entry['heading'])) + r'\s+bilhete\s*:\s*(\d+(?:[.,]\d{1,2})?)\s+eur', page)
                if len(set(prices)) == 1:
                    record.update(status='verified', adult_entry_eur=prices[0].replace(',', '.'), price_scope='standard adult admission; discounts and availability not checked')
        except (OSError, ValueError, UnicodeError):
            pass
        facts[key] = record
    return facts
