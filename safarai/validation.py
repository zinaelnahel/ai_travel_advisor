"""Conservative output checks; estimates are not verified prices or routes."""
import re
import json
from decimal import Decimal
import unicodedata
from datetime import date, timedelta


def normalized(text):
    return ''.join(c for c in unicodedata.normalize('NFKD', text) if not unicodedata.combining(c)).lower()


def explicit_meal_allowances(messages):
    """Read explicit per-diner amounts from user text; never assistant estimates."""
    allowances = {}
    number = r'\d+(?:\.\d{1,2})?'
    for message in messages:
        if message.get('role') != 'user':
            continue
        text = message.get('content') or ''
        # Group meal allowances need clarification rather than a guessed split.
        if re.search(r'\b(?:per group|for (?:both|the group))\b', text, re.I):
            continue
        currencies = re.findall(r'\b(?:EGP|EUR|USD|GBP|DKK|JPY)\b|€', text, re.I)
        currency = currencies[0].upper().replace('€', 'EUR') if len(set(c.upper() for c in currencies)) == 1 else None
        if not currency:
            continue
        for meal in ('breakfast', 'lunch', 'dinner'):
            match = re.search(r'\b' + meal + r'(?:s)?\s*(?:allowance|budget|cost)?\s*(?:of|at|is|to|=|:)?\s*(?:' + currency + r'\s*|€\s*)?(' + number + r')\b', text, re.I)
            if match:
                allowances[meal] = {'currency': currency, 'amount': match[1]}
        if all(re.search(r'\b' + meal + r'\b', text, re.I) for meal in ('breakfast', 'lunch', 'dinner')):
            triple = re.search(r'(?:' + currency + r'\s*|€\s*)(' + number + r')\s*/\s*(' + number + r')\s*/\s*(' + number + r')', text, re.I)
            if triple:
                order = sorted(('breakfast', 'lunch', 'dinner'), key=lambda meal: re.search(r'\b' + meal + r'\b', text, re.I).start())
                for meal, amount in zip(order, triple.groups()):
                    allowances[meal] = {'currency': currency, 'amount': amount}
    return allowances


def validation_reason(error):
    """Classify failures without logging model output or private trip data."""
    if isinstance(error, json.JSONDecodeError):
        return 'ledger_json', 'The cost ledger was not valid JSON.'
    if isinstance(error, KeyError):
        return 'missing_field', 'A required cost field was missing.'
    if isinstance(error, ArithmeticError):
        return 'invalid_amount', 'A generated cost amount could not be read as a valid number.'
    if isinstance(error, TypeError):
        return 'field_type', 'A generated budget field had an invalid format.'
    message = str(error).lower()
    rules = [
        ('must cover all', 'meal_coverage', 'The generated plan omitted or duplicated meals for one or more travelers.'),
        ('distinct trip day', 'meal_days', 'The generated meal rows had missing or repeated trip days.'),
        ('meal day is outside', 'meal_days', 'A meal was assigned outside the trip dates.'),
        ('excluded meal', 'meal_exclusion', 'An excluded meal conflicted with the itinerary.'),
        ('sourced menu differs', 'meal_allowance_conflict', 'A restaurant menu conflicted with your stated meal allowance.'),
        ('exact sourced menu', 'menu_option', 'A restaurant cost did not match the researched menu option.'),
        ('dated menu', 'menu_dates', 'A dated menu price could not be matched to the trip dates.'),
        ('planned meal date', 'menu_dates', 'A researched menu price was not valid on a planned meal date.'),
        ('budget cap', 'budget_cap', 'The generated budget cap was missing its amount or group/per-person basis, or contained an invalid amount.'),
        ('unit_basis', 'cost_basis', 'A generated cost did not specify whether it covered one person or the group.'),
        ('occurrences count', 'cost_occurrences', 'A generated cost did not specify a valid number of purchases or uses.'),
        ('cost or quantity', 'invalid_amount', 'A generated cost amount or quantity was outside the permitted range.'),
        ('cost item people', 'item_party_size', 'A cost item used an invalid traveler count.'),
        ('cost items', 'cost_items', 'The generated budget did not contain a valid list of cost items.'),
        ('cost item must', 'cost_items', 'A generated cost item had an invalid format.'),
        ('meal', 'meal_coverage', 'Meal quantities or menu details could not be validated.'),
        ('transport', 'transport_fare', 'The transport ticket did not match its source.'),
        ('admission', 'admission', 'Admission quantities or source prices could not be validated.'),
        ('city-only', 'city_boundary', 'A venue was outside the requested city.'),
        ('closed', 'venue_closed', 'The plan included a closed venue.'),
        ('markdown', 'response_format', 'The response was not a readable itinerary.'),
        ('code blocks', 'response_format', 'The response exposed internal structured data.'),
        ('traveler', 'party_size', 'The traveler count did not match the trip.'),
        ('day count', 'trip_duration', 'The budget duration did not match the trip.'),
        ('duplicate cost item', 'cost_item', 'A cost item was duplicated or could not be displayed.'),
        ('currency', 'currency', 'The cost currency could not be validated.'),
    ]
    for fragment, code, description in rules:
        if fragment in message:
            return code, description
    return 'cost_ledger', 'The cost breakdown could not be validated.'


def validate_plan_format(text, expected_days=None):
    """Only readable Markdown plus one internal ledger may reach rendering."""
    ledgers = list(re.finditer(r'```budget-json\s*\n.*?\n```', text, re.S))
    if len(ledgers) != 1:
        raise ValueError('Return a Markdown itinerary with exactly one fenced budget-json ledger, not a JSON response.')
    prose = text[:ledgers[0].start()] + text[ledgers[0].end():]
    if not re.search(r'(?im)^###\s+Day\s+\d+', prose):
        raise ValueError('The Markdown itinerary needs readable ### Day N sections.')
    if expected_days is not None:
        day_numbers = [int(day) for day in re.findall(r'(?im)^###\s+Day\s+(\d+)\b', prose)]
        if day_numbers != list(range(1, expected_days + 1)):
            raise ValueError('The Markdown day count must match the trip, in order, without duplicates.')
    if re.search(r'(?m)^\s*(?:[\[{]|```)', prose):
        raise ValueError('Internal JSON or code blocks must not appear outside the budget-json ledger.')


def remove_weather_claims(text):
    # Model-written weather prose cannot establish dates or numeric evidence.
    # Replace whole matching lines; source observations are appended separately in code.
    pattern = r'\b(weather|forecast|rain|rainfall|precipitation|temperature|sunny|celsius|fahrenheit)\b|\u00b0[CF]'
    lines = text.splitlines()
    # Budget tables have already been calculated: filtering a row would make
    # the visible line items disagree with their totals.
    removed = any(not line.lstrip().startswith('|') and re.search(pattern, line, re.I) for line in lines)
    clean = '\n'.join(line for line in lines if line.lstrip().startswith('|') or not re.search(pattern, line, re.I))
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


def budget_context(data, trip):
    travelers = data.get('travelers', 1)
    days = data.get('days', (trip or {}).get('days', 1))
    if type(travelers) is not int or not 1 <= travelers <= 20 or type(days) is not int or not 1 <= days <= 30:
        raise ValueError('Budget needs a valid traveler count and number of days.')
    if trip and trip.get('travelers', travelers) != travelers:
        raise ValueError('Budget traveler count differs from the trip brief.')
    if trip and trip.get('days', days) != days:
        raise ValueError('Budget day count differs from the trip brief.')
    group = data.get('cost_basis') == 'group'
    if travelers > 1 and not group:
        raise ValueError('Multiple travelers require cost_basis=group, explicit unit_basis and occurrences for each item.')
    return travelers, days, group


def item_units(item, travelers, days, group, meals, admission=False):
    if not group:
        return Decimal(str(item['quantity']))
    basis = item.get('unit_basis')
    if basis not in ('per_person', 'per_group'):
        raise ValueError('Each group cost needs unit_basis=per_person or per_group.')
    people = item.get('people', travelers)
    if type(people) is not int or not 1 <= people <= travelers:
        raise ValueError('Cost item people must be within the traveler count.')
    if item.get('meal'):
        meal = item['meal']
        covered_days = item.get('days')
        if meal not in ('breakfast', 'lunch', 'dinner') or not isinstance(covered_days, list) or not covered_days or len(set(covered_days)) != len(covered_days):
            raise ValueError('Meals need breakfast/lunch/dinner and distinct trip day numbers.')
        if any(type(day) is not int or not 1 <= day <= days for day in covered_days):
            raise ValueError('Meal day is outside the trip.')
        for day in covered_days:
            meals[day, meal] = meals.get((day, meal), 0) + people
        occurrences = len(covered_days)
    elif admission or item.get('visit_days') is not None:
        visits = item.get('visit_days')
        if visits is None:
            # Older single-visit ledgers remain valid; repeated admissions need
            # explicit visit days rather than an unexplained trip-day multiplier.
            if item.get('occurrences') != 1:
                raise ValueError('Admission costs require visit_days listing actual visits, not trip duration or traveler count.')
            occurrences = 1
        else:
            if not isinstance(visits, list) or not visits or any(type(day) is not int or not 1 <= day <= days for day in visits) or len(set(visits)) != len(visits):
                raise ValueError('Admission visit_days must contain distinct valid trip days.')
            occurrences = len(visits)
    else:
        if item.get('category', '').lower() in ('food', 'meals', 'dining', 'restaurants') and not item.get('supplement'):
            raise ValueError('Main food costs must identify their meal and days; mark snacks as supplement=true.')
        occurrences = item.get('occurrences')
        if type(occurrences) is not int or not 1 <= occurrences <= 1000:
            raise ValueError('Each non-meal cost needs a positive integer occurrences count.')
    return Decimal(occurrences * (people if basis == 'per_person' else 1))


def meal_coverage(data, meals, travelers, days):
    exclusions = data.get('meal_exclusions', [])
    if not isinstance(exclusions, list):
        raise ValueError('Meal exclusions must be a list.')
    notes = []
    for excluded in exclusions:
        if not isinstance(excluded, dict):
            raise ValueError('Each meal exclusion must be an object.')
        reason = excluded.get('reason')
        if not isinstance(reason, str) or not 1 <= len(reason) <= 200 or excluded.get('meal') not in ('breakfast', 'lunch', 'dinner'):
            raise ValueError('Excluded meals need a meal type and an explicit reason.')
        for day in excluded.get('days', []):
            if type(day) is not int or not 1 <= day <= days or (day, excluded['meal']) in meals:
                raise ValueError('Excluded meal conflicts with a listed meal or trip day.')
            meals[day, excluded['meal']] = travelers
            notes.append(f"Day {day} {excluded['meal']}: {reason}")
    problems = []
    for day in range(1, days+1):
        for meal in ('breakfast', 'lunch', 'dinner'):
            if meals.get((day, meal), 0) != travelers:
                problems.append(f'day {day} {meal} covers {meals.get((day, meal), 0)}/{travelers}')
    if problems:
        # Report every gap in one repair instead of discovering another missing
        # meal only after the sole repair attempt has already been spent.
        raise ValueError('Meals must cover all travelers exactly once or have an explicit meal exclusion: ' + '; '.join(problems) + '.')
    return notes


def structured_budget(text, facts, trip=None):
    """Calculate quantities and group/per-person totals from one cost ledger."""
    match = re.search(r'```budget-json\s*\n(.*?)\n```', text, re.S)
    if not match:
        raise ValueError('Missing structured cost ledger.')
    data = json.loads(match[1], parse_float=Decimal)
    if not isinstance(data, dict):
        raise ValueError('Budget ledger must be an object.')
    travelers, days, group = budget_context(data, trip)
    currency = data['currency']
    if not re.fullmatch(r'[A-Z]{3}', currency):
        raise ValueError('Invalid ledger currency.')
    items = data['items']
    if not isinstance(items, list) or not 1 <= len(items) <= 100:
        raise ValueError('Missing or excessive cost items.')
    rows, categories, identifiers, sources, admissions = [], {}, set(), set(), set()
    meals = {}
    for fact in facts.values():
        if trip and trip.get('city_only') and fact.get('city') and normalized(fact['city']) != normalized(trip['destination']) and fact.get('name') and normalized(fact['name']) in normalized(text):
            raise ValueError('A venue is outside the requested city-only boundary; remove it from the plan and ledger.')
        if fact.get('operating_status') == 'permanently_closed' and fact.get('name') and normalized(fact['name']) in normalized(text[:match.start()]):
            raise ValueError('A permanently closed venue is still recommended; remove it and revise the ledger.')
    for index, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError('Every cost item must be an object.')
        # A display-row ID is bookkeeping, not evidence. Generate it if the
        # model supplies only venue_id; never invent a price or quantity.
        identifier, label, category = item.get('id', f'cost_{index}'), item['label'], item['category']
        if not all(isinstance(v, str) and re.fullmatch(r"[\w .,:()/'\u2019&+\-\u2013\u2014]{1,100}", v) for v in (identifier, label, category)) or identifier in identifiers:
            raise ValueError('Invalid or duplicate cost item.')
        identifiers.add(identifier)
        cost = Decimal(str(item['unit_cost']))
        basis = 'planning estimate'
        matching = [key for key, fact in facts.items() if fact.get('unit_price') is not None and normalized(fact.get('name', '')) and normalized(fact['name']) in normalized(label)]
        exterior_only = bool(re.search(r'\b(exterior|outside|exterior-only)\b', label, re.I)) and cost == 0
        venue_id = item.get('venue_id') or (matching[0] if matching and not exterior_only else None)
        admission = (item.get('kind') == 'admission'
                     or (category.lower() != 'transport' and bool(re.search(r'\b(admission|entry|tickets?)\b', label, re.I)))
                     or facts.get(venue_id, {}).get('price_kind') == 'admission')
        quantity = item_units(item, travelers, days, group, meals, admission=admission)
        if not cost.is_finite() or not quantity.is_finite() or cost < 0 or not 0 < quantity <= 1000 or cost > 1000000:
            raise ValueError('Invalid cost or quantity.')
        if venue_id:
            fact = facts.get(venue_id, {})
            if fact.get('operating_status') == 'permanently_closed':
                raise ValueError('A permanently closed venue cannot be included in the cost ledger.')
            if fact.get('status') == 'web_sourced' and fact.get('currency') == currency and 'unit_price' in fact:
                if group and item.get('unit_basis') != 'per_person':
                    raise ValueError('Sourced adult ticket and individual menu prices require unit_basis=per_person.')
                if fact.get('price_kind') == 'transport':
                    if not item.get('price_option') or item['price_option'] != fact.get('price_option'):
                        raise ValueError('Transport must match the exact sourced fare, zones and duration using price_option.')
                    if fact.get('valid_from') or fact.get('valid_until'):
                        if not data.get('start_date'):
                            raise ValueError('Dated transport fares require a trip start_date.')
                        start = date.fromisoformat(data['start_date'])
                        end = (start + timedelta(days=days-1)).isoformat()
                        if fact.get('valid_from') and start.isoformat() < fact['valid_from'] or fact.get('valid_until') and end > fact['valid_until']:
                            raise ValueError('Transport fare validity does not cover the trip.')
                if fact.get('price_kind') == 'meal':
                    if not item.get('meal') or not item.get('price_option') or item['price_option'] != fact.get('price_option'):
                        raise ValueError('The restaurant cost must match the exact sourced menu option.')
                    if fact.get('valid_from') or fact.get('valid_until'):
                        if not data.get('start_date'):
                            raise ValueError('Dated menu prices require a trip start_date or an unsourced meal estimate.')
                        start = date.fromisoformat(data['start_date'])
                        for day in item.get('days', []):
                            visit = (start + timedelta(days=day-1)).isoformat()
                            if fact.get('valid_from') and visit < fact['valid_from'] or fact.get('valid_until') and visit > fact['valid_until']:
                                raise ValueError('Sourced menu price does not cover the planned meal date.')
                cost = Decimal(fact['unit_price'])
                basis = 'web-sourced reference, checked ' + fact['checked_at'][:10]
            elif fact.get('status') == 'web_sourced' and category.lower() in ('food', 'meals', 'dining', 'restaurants') and not item.get('price_option') and not re.search(r'entry|admission|ticket', label, re.I):
                basis = 'meal estimate; venue location web-sourced'
            else:
                raise ValueError('Official admission price unavailable; omit the paid interior visit or revise the plan.')
            admissions.add(venue_id)
            title = re.sub(r'[\[\]()*_`<>]', '', fact['name'])
            sources.add(f"[{title}]({fact['source_url']})")
        allowance = (trip or {}).get('meal_allowances', {}).get(item.get('meal'))
        if allowance:
            if allowance['currency'] != currency or item.get('unit_basis') != 'per_person':
                raise ValueError('User meal allowances require matching currency and per-person units.')
            supplied_cost = Decimal(allowance['amount'])
            if not supplied_cost.is_finite() or not 0 <= supplied_cost <= 1000000:
                raise ValueError('User meal allowance must be a finite nonnegative amount within the cost limit.')
            if venue_id and facts.get(venue_id, {}).get('price_kind') == 'meal' and facts[venue_id].get('unit_price') is not None and cost != supplied_cost:
                raise ValueError('The sourced menu differs from the user meal allowance; choose an unsourced allowance row or a compatible menu.')
            cost = supplied_cost
            basis = 'user-specified meal allowance; not a verified menu price'
        cost = cost.quantize(Decimal('0.01'))
        subtotal = (cost * quantity).quantize(Decimal('0.01'))
        categories[category] = categories.get(category, Decimal(0)) + subtotal
        if group:
            basis = ('unit price per person; ' if item['unit_basis'] == 'per_person' else 'unit price per group; ') + basis
        rows.append(f'| {label} | {currency} {cost:.2f} | {quantity} | {currency} {subtotal:.2f} | {basis} |')
    for key, fact in facts.items():
        if fact.get('unit_price') is None or key in admissions or not fact.get('name'):
            continue
        mentions = [line for line in text[:match.start()].splitlines() if normalized(fact['name']) in normalized(line)]
        if any(not re.search(r'\b(exterior|outside|exterior-only)\b', line, re.I) for line in mentions):
            raise ValueError('A mentioned admission is missing from the ledger; include it or specify exterior-only.')
    total = sum(categories.values())
    exclusions = meal_coverage(data, meals, travelers, days) if group else []
    group_total = total if group else total * travelers
    per_person = (group_total / travelers).quantize(Decimal('0.01'))
    # Eliminate independent model totals/taxi fares elsewhere. The named ledger
    # items below retain every numerical cost and explain category differences.
    narrative = re.split(r'(?im)^## Estimated budget\s*$', text[:match.start()])[0]
    narrative = '\n'.join(line for line in narrative.splitlines() if not re.search(r'\b(exchange rate|currency conversion)\b', line, re.I))
    currencies = '|'.join(sorted({currency, 'EUR', 'USD', 'GBP'}))
    narrative = re.sub(r'(?:' + currencies + r'|\u20ac|\$|\u00a3)\s*\d+(?:[.,]\d+)*(?:\s*[-\u2013]\s*\d+(?:[.,]\d+)*)?', 'see cost ledger', narrative)
    narrative = re.sub(r'\d+(?:[.,]\d+)*\s*(?:' + currencies + r')\b', 'see cost ledger', narrative)
    narrative = re.sub(r'(?i)\b(?:admission|entry|ticket price)\s+is\s+see cost ledger(?:\s+per person)?', 'Admission costs are listed in the budget below', narrative)
    party = f'{travelers} traveler' + ('s' if travelers != 1 else '')
    conclusion = f'Estimated group total for {party}: {currency} {group_total:.2f}; per person: {currency} {per_person:.2f}.'
    cap_data = data.get('cap')
    cap = None
    if cap_data is not None:
        if not isinstance(cap_data, dict):
            raise ValueError('Budget cap must specify an amount and basis.')
        if cap_data.get('basis') not in ('group', 'per_person'):
            raise ValueError('Budget cap must specify group or per_person basis.')
        cap = Decimal(str(cap_data['amount'])) * (travelers if cap_data['basis'] == 'per_person' else 1)
    elif data.get('cap_per_person') is not None:
        cap = Decimal(str(data['cap_per_person'])) * travelers
    if cap is not None:
        if not cap.is_finite() or cap <= 0:
            raise ValueError('Invalid budget cap.')
        conclusion += f' Group cap: {currency} {cap:.2f}. ' + (f'Over cap by {currency} {group_total-cap:.2f}; this plan does not meet the cap.' if group_total > cap else f'Remaining buffer: {currency} {cap-group_total:.2f}.')
    if group:
        summary = f"{days}-day trip for {party}" + (f" to {trip['destination']}" if trip and trip.get('destination') else '') + '.'
        if trip and trip.get('interests'):
            summary += ' Interests: ' + ', '.join(trip['interests']) + '.'
        summary += ' ' + conclusion
        pattern = r'(?ims)^## Trip summary[^\n]*\n.*?(?=^## |\Z)'
        if re.search(pattern, narrative):
            narrative = re.sub(pattern, lambda _: '## Trip summary\n' + summary + '\n\n', narrative, count=1)
        else:
            narrative = '## Trip summary\n' + summary + '\n\n' + narrative
    scope = f'Line subtotals cover the whole group of {travelers} for {days} days. Units are calculated from people and occurrences.' if group else 'Line subtotals are per person for the whole trip.'
    output = [narrative.rstrip(), '## Estimated budget', scope + ' All estimates are planning references, not quotes.', '| Item | Unit cost | Units | Subtotal | Basis |', '|---|---:|---:|---:|---|', *rows, '', '| Category | Estimated cost |', '|---|---:|']
    output += [f'| {category} | {currency} {amount:.2f} |' for category, amount in categories.items()]
    if group:
        output.append(f'| **Total group ({party})** | **{currency} {group_total:.2f}** |')
    output.append(f'| **Total per person** | **{currency} {per_person:.2f}** |')
    # A blank line terminates the Markdown table. Without it, the renderer
    # consumes conclusions and source notes as malformed one-cell table rows.
    output.extend(['', conclusion])
    if exclusions:
        output.extend(['', 'Meals outside this estimate: ' + '; '.join(exclusions) + '.'])
    output += ['', 'Only listed items are included; unlisted flights, insurance and other expenses are excluded. Confirm eligibility, prices and availability before booking.']
    if sources:
        output.extend(['', 'Sources: ' + ', '.join(sorted(sources))])
    return '\n\n'.join([output[0], output[1], output[2], '\n'.join(output[3:])])
