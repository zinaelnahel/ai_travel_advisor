"""Conservative output checks; estimates are not verified prices or routes."""
import re
import json
from decimal import Decimal
from venue_sources import CATALOG, normalized


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
    # Do not splice replacement fragments into sentences. Remove an unsupported
    # proximity modifier; keep one application-owned disclaimer.
    text = re.sub(r'\b(?:a\s+)?(?:\d+|five|ten|fifteen|twenty|thirty)[ -](?:minute|min)[ -](?:walk|ride|drive)\b', 'transfer', text, flags=re.I)
    text = re.sub(r'\bnearby\s*|\bwithin walking distance\s*', '', text, flags=re.I)
    text = re.sub(r'(?im)^.*(?:\*\*Route and access check:|Suggested stops are not verified routes).*(?:\n|$)', '', text)
    return text.rstrip() + '\n\n**Route and access check:** Suggested stops are not verified routes. Confirm venue addresses, travel times, opening hours, step-free access and facilities with a map and each venue before relying on this plan.'


def structured_budget(text, facts):
    """Render one ledger in code; all line items are per person for the full trip."""
    match = re.search(r'```budget-json\s*\n(.*?)\n```', text, re.S)
    if not match:
        raise ValueError('Missing structured cost ledger.')
    data = json.loads(match[1], parse_float=Decimal)
    currency = data['currency']
    if not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Invalid ledger currency.')
    items = data['items']
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('Missing or excessive cost items.')
    rows, categories, identifiers, sources, admissions = [], {}, set(), set(), set()
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Every cost item must be an object.')
        # A display-row ID is bookkeeping, not evidence. Generate it if the
        # model supplies only venue_id; never invent a price or quantity.
        identifier, label, category = item.get('id', f'cost_{index}'), item['label'], item['category']
        if not all(isinstance(v, str) and re.fullmatch(r"[\w .,:()/'&+\-]{1,100}", v) for v in (identifier, label, category)) or identifier in identifiers:
            raise ValueError('Invalid or duplicate cost item.')
        identifiers.add(identifier)
        cost, quantity = Decimal(str(item['unit_cost'])), Decimal(str(item['quantity']))
        if not cost.is_finite() or not quantity.is_finite() or cost < 0 or not 0 < quantity <= 1000 or cost > 1000000:
            raise ValueError('Invalid cost or quantity.')
        basis = 'planning estimate'
        matching = [key for key, entry in CATALOG.items() if 'heading' in entry and any(alias in normalized(label) for alias in entry['aliases'])]
        matching += [key for key, fact in facts.items() if fact.get('unit_price') is not None and normalized(fact.get('name', '')) and normalized(fact['name']) in normalized(label) and key not in matching]
        exterior_only = bool(re.search(r'\b(exterior|outside|exterior-only)\b', label, re.I)) and cost == 0
        venue_id = item.get('venue_id') or (matching[0] if matching and not exterior_only else None)
        if venue_id:
            fact = facts.get(venue_id, {})
            if fact.get('status') == 'web_sourced' and fact.get('currency') == currency and 'unit_price' in fact:
                cost = Decimal(fact['unit_price'])
                basis = 'web-sourced reference, checked ' + fact['checked_at'][:10]
            elif fact.get('status') == 'web_sourced' and category.lower() in ('food', 'meals', 'dining', 'restaurants') and not re.search(r'entry|admission|ticket', label, re.I):
                basis = 'meal estimate; venue location web-sourced'
            elif currency == 'EUR' and fact.get('status') == 'verified' and 'adult_entry_eur' in fact:
                cost = Decimal(fact['adult_entry_eur'])
                basis = 'standard adult entry, checked ' + fact['checked_at'][:10]
            else:
                raise ValueError('Official admission price unavailable; omit the paid interior visit or revise the plan.')
            admissions.add(venue_id)
            title = re.sub(r'[\[\]()*_`<>]', '', fact['name'])
            sources.add(f"[{title}]({fact['source_url']})")
        cost = cost.quantize(Decimal('0.01'))
        subtotal = (cost * quantity).quantize(Decimal('0.01'))
        categories[category] = categories.get(category, Decimal(0)) + subtotal
        rows.append(f'| {label} | {currency} {cost:.2f} | {quantity} | {currency} {subtotal:.2f} | {basis} |')
    for key, entry in CATALOG.items():
        if 'heading' not in entry or key in admissions:
            continue
        mentions = [line for line in text[:match.start()].splitlines() if any(alias in normalized(line) for alias in entry['aliases'])]
        if any(not re.search(r'\b(exterior|outside|exterior-only)\b', line, re.I) for line in mentions):
            raise ValueError('A mentioned admission is missing from the ledger; include it or specify exterior-only.')
    total = sum(categories.values())
    # Eliminate independent model totals/taxi fares elsewhere. The named ledger
    # items below retain every numerical cost and explain category differences.
    narrative = re.split(r'(?im)^## Estimated budget\s*$', text[:match.start()])[0]
    narrative = re.sub(r'(?:EUR|USD|GBP|\u20ac|\$|\u00a3)\s*\d+(?:[.,]\d+)*(?:\s*[-\u2013]\s*\d+(?:[.,]\d+)*)?', 'see cost ledger', narrative)
    narrative = re.sub(r'\d+(?:[.,]\d+)*\s*(?:EUR|USD|GBP)\b', 'see cost ledger', narrative)
    output = [narrative.rstrip(), '## Estimated budget', 'All line items are per person for the entire trip. Quantities must allocate shared costs per person. Estimated prices are not quotes.', '| Item | Unit cost | Quantity | Subtotal | Basis |', '|---|---:|---:|---:|---|', *rows, '', '| Category | Estimated cost |', '|---|---:|']
    output += [f'| {category} | {currency} {amount:.2f} |' for category, amount in categories.items()]
    output.append(f'| **Total per person** | **{currency} {total:.2f}** |')
    if data.get('cap_per_person') is not None:
        cap = Decimal(str(data['cap_per_person']))
        if not cap.is_finite() or cap <= 0:
            raise ValueError('Invalid budget cap.')
        output.append(f'Planning cap per person: {currency} {cap:.2f}. ' + (f'Over cap by {currency} {total-cap:.2f}; this plan does not meet the cap.' if total > cap else f'Remaining buffer: {currency} {cap-total:.2f}.'))
    output += ['Only listed items are included; unlisted flights, insurance and other expenses are excluded. Confirm eligibility, prices and availability before booking.']
    if sources:
        output.append(('Admission sources: ' if any(f.get('status') == 'web_sourced' for f in facts.values()) else 'Official admission sources: ') + ', '.join(sorted(sources)))
    return '\n\n'.join([output[0], output[1], output[2], '\n'.join(output[3:])])
