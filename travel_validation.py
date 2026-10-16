"""Conservative output checks; estimates are not verified prices or routes."""
import re
from decimal import Decimal


def calculated_budget(text):
    match = re.search(r"(?im)^## Estimated budget\s*$", text)
    if not match:
        return text
    prefix = text[:match.start()]
    rows = []
    currency = None
    for line in text[match.end():].splitlines():
        if not line.strip().startswith('|'):
            continue
        cells = [c.strip().replace('**', '') for c in line.strip().strip('|').split('|')]
        if len(cells) != 2:
            return prefix + unavailable_budget()
        category, value = cells
        if category.lower() in ('category', 'total') or re.fullmatch(r'[-: ]+', category):
            continue
        # One currency and unambiguous dot-decimal amounts only; reject ambiguous formats.
        value = value.replace('\u20ac', 'EUR').replace('\u00a3', 'GBP').replace('\u2013', '-').replace('\u2014', '-')
        parsed = re.fullmatch(r'(EUR|USD|GBP|\$)\s*(\d+(?:\.\d{1,2})?)(?:\s*-\s*(\d+(?:\.\d{1,2})?))?', value)
        if not parsed:
            return prefix + unavailable_budget()
        code = {'$':'USD'}.get(parsed[1], parsed[1])
        if currency and code != currency:
            return prefix + unavailable_budget()
        currency = code
        low, high = Decimal(parsed[2]), Decimal(parsed[3] or parsed[2])
        if high < low or re.search(r'total|person|group|subtotal', category, re.I):
            return prefix + unavailable_budget()
        rows.append((category, low, high))
    if not rows or len({r[0].lower() for r in rows}) != len(rows):
        return prefix + unavailable_budget()
    def amount(low, high):
        return f'{currency} {low:.2f}' + (f'-{high:.2f}' if high != low else '')
    result = ['## Estimated budget', 'Approximate estimates per person for the whole trip. Totals calculated from the listed categories; not live quotes.', '| Category | Estimated cost |', '|---|---:|']
    result += [f'| {name} | {amount(low, high)} |' for name, low, high in rows]
    result.append(f'| **Total per person** | **{amount(sum(r[1] for r in rows), sum(r[2] for r in rows))}** |')
    result.append('Only listed categories are included. Unlisted flights, insurance, taxes and other expenses are excluded. Confirm prices and availability before booking.')
    return prefix + '\n\n'.join([result[0], result[1], '\n'.join(result[2:-1]), result[-1]])


def unavailable_budget():
    return '## Estimated budget\n\nThe cost estimates could not be validated. Ask for a revised budget with one currency and an amount or range for each category. No total is shown.\n'


def remove_weather_claims(text):
    # Model-written weather prose cannot establish dates or numeric evidence.
    # Replace whole matching lines; source observations are appended separately in code.
    pattern = r'\b(weather|forecast|rain|rainfall|precipitation|temperature|sunny|celsius|fahrenheit)\b|\u00b0[CF]'
    lines = text.splitlines()
    removed = any(re.search(pattern, line, re.I) for line in lines)
    clean = '\n'.join(line for line in lines if not re.search(pattern, line, re.I))
    if removed:
        clean += '\n\nWeather guidance: only dated provider observations below are available. No prediction is provided for dates outside that period.'
    return clean


def qualify_routes(text):
    text = re.sub(r'\b(?:a\s+)?(?:\d+|five|ten|fifteen|twenty|thirty)[ -](?:minute|min)[ -](?:walk|ride|drive)\b', 'transfer (travel time unverified)', text, flags=re.I)
    text = re.sub(r'\bnearby\b|\bwithin walking distance\b', 'location to verify', text, flags=re.I)
    return text + '\n\n**Route and access check:** Suggested stops are not verified routes. Confirm venue addresses, travel times, opening hours, step-free access and facilities with a map and each venue before relying on this plan.'
