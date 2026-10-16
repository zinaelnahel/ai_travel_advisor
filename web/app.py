"""Stateless FastAPI interface; run with python -m web.app."""
import base64
import hmac
import os
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, Request, Query
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.concurrency import run_in_threadpool

from advisor import TravelAdvisor, safe_markdown, request_json
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')
STATE_FIELDS = ('history', 'summary', 'metrics', 'last_itinerary', 'last_brief', 'summary_count')
MAX_BODY = 1_000_000


def create_app(advisor_factory=None):
    app = FastAPI(title='SafarAI API', docs_url=None, redoc_url=None)
    app.mount('/static', StaticFiles(directory=ROOT / 'web/static'), name='static')
    templates = Jinja2Templates(directory=ROOT / 'web/templates')

    @app.get('/docs', include_in_schema=False, response_class=HTMLResponse)
    def docs():
        return HTMLResponse('''<!DOCTYPE html>
<html><head><title>SafarAI API — Swagger UI</title>
<link rel="stylesheet" href="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui.css">
</head><body><div id="swagger-ui"></div>
<script src="https://cdn.jsdelivr.net/npm/swagger-ui-dist@5.17.14/swagger-ui-bundle.js"></script>
<script src="/static/swagger.js"></script></body></html>''')

    def serializer():
        secret = os.getenv('STATE_SIGNING_SECRET', '')
        if len(secret) < 32:
            raise RuntimeError('Set STATE_SIGNING_SECRET to a random value of at least 32 characters.')
        return URLSafeTimedSerializer(secret, salt='safarai-state-v1')

    @app.middleware('http')
    async def protect(request: Request, call_next):
        response = None
        password = os.getenv('APP_PASSWORD', '')
        if password and request.url.path != '/health':
            try:
                scheme, encoded = request.headers.get('authorization', '').split(' ', 1)
                credentials = base64.b64decode(encoded, validate=True).decode().split(':', 1)
                valid = scheme.lower() == 'basic' and len(credentials) == 2 and hmac.compare_digest(credentials[1].encode(), password.encode())
            except (ValueError, UnicodeError):
                valid = False
            if not valid:
                response = JSONResponse({'error': 'Sign in to your travel advisor.'}, 401,
                                        headers={'WWW-Authenticate': 'Basic realm="Travel Advisor"'})
        if response is None and request.url.path.startswith('/api/') and request.method == 'POST':
            origin = request.headers.get('origin')
            expected = str(request.base_url).rstrip('/')
            # Vercel terminates TLS before forwarding requests to the function.
            if os.getenv('VERCEL') == '1':
                expected = 'https://' + request.url.netloc
            if request.headers.get('x-travel-request') != '1' or (origin and origin != expected):
                response = JSONResponse({'error': 'Cross-site or invalid request.'}, 403)
        if response is None:
            response = await call_next(request)
        response.headers.update({
            'Cache-Control': 'no-store', 'X-Content-Type-Options': 'nosniff',
            'Referrer-Policy': 'same-origin',
            'Content-Security-Policy': "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'",
        })
        if request.url.path == '/docs':
            response.headers['Content-Security-Policy'] = (
                "default-src 'self'; img-src 'self' data:; "
                "style-src 'self' 'unsafe-inline' https://cdn.jsdelivr.net; "
                "script-src 'self' https://cdn.jsdelivr.net; "
                "frame-ancestors 'none'; base-uri 'self'"
            )
        return response

    @app.get('/')
    def home(request: Request):
        return templates.TemplateResponse(request=request, name='index.html',
                                          context={'configured': bool(os.getenv('OPENAI_API_KEY'))})

    @app.get('/health')
    def health():
        return {'status': 'ok'}

    def public_data(url):
        try:
            return request_json(url, timeout=10)
        except (RuntimeError, ValueError):
            return JSONResponse({'error': 'The public data provider is unavailable. Please try again.'}, 502)

    @app.get('/api/places')
    def places(name: str = Query(min_length=2, max_length=100)):
        return public_data('https://geocoding-api.open-meteo.com/v1/search?' + urlencode(
            {'name': name, 'count': 5, 'language': 'en', 'format': 'json'}))

    @app.get('/api/forecast')
    def forecast(latitude: float = Query(ge=-90, le=90), longitude: float = Query(ge=-180, le=180)):
        return public_data('https://api.open-meteo.com/v1/forecast?' + urlencode({
            'latitude': latitude, 'longitude': longitude, 'timezone': 'auto', 'forecast_days': 16,
            'daily': 'temperature_2m_max,temperature_2m_min,precipitation_probability_max'}))

    @app.get('/api/rate')
    def rate(currency: str = Query(pattern='^[A-Z]{3}$')):
        return public_data('https://api.frankfurter.dev/v2/rate/EUR/' + currency)

    def process(action, body):
        try:
            signer = serializer()
        except RuntimeError as exc:
            return JSONResponse({'error': str(exc)}, 503)
        if not advisor_factory and not os.getenv('OPENAI_API_KEY'):
            return JSONResponse({'error': 'Configure OPENAI_API_KEY on the server.'}, 503)
        advisor = advisor_factory() if advisor_factory else TravelAdvisor(
            os.environ['OPENAI_API_KEY'], budget_usd=float(os.getenv('CONVERSATION_BUDGET_USD', '1')))
        token = body.get('state')
        if token is not None and action != 'reset':
            try:
                if not isinstance(token, str) or len(token) > MAX_BODY:
                    raise ValueError()
                state = signer.loads(token, max_age=3600)
                if not isinstance(state, dict) or set(state) != set(STATE_FIELDS):
                    raise ValueError()
                for field in STATE_FIELDS:
                    setattr(advisor, field, state[field])
            except (BadData, ValueError, TypeError):
                return JSONResponse({'error': 'Trip state is invalid or expired. Start a new trip.'}, 400)
        try:
            if action == 'reset':
                advisor.reset()
                result = {'reset': True}
            elif action == 'image':
                if not advisor.last_itinerary:
                    return JSONResponse({'error': 'Create an itinerary first.'}, 400)
                result = {'image': 'data:image/png;base64,' + base64.b64encode(advisor.generate_image()).decode('ascii')}
            else:
                text = body.get('message')
                if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                    return JSONResponse({'error': 'Enter a message of 1-6,000 characters.'}, 400)
                previous_plan, previous_brief = advisor.last_itinerary, advisor.last_brief
                reply = advisor.reply(text)
                plan = advisor.last_itinerary
                result = {'reply': safe_markdown(reply), 'itinerary': plan,
                          'itinerary_html': safe_markdown(plan) if plan else None,
                          'destination': (advisor.last_brief or {}).get('destination')}
                if not plan:
                    advisor.last_itinerary, advisor.last_brief = previous_plan, previous_brief
            result['usage_html'] = advisor.usage_summary_html()
            result['state'] = signer.dumps({field: getattr(advisor, field) for field in STATE_FIELDS})
            return JSONResponse(result)
        except (RuntimeError, ValueError, KeyError, TypeError):
            return JSONResponse({'error': 'The request failed. Check API access or quota, or try a shorter request. Your itinerary remains available.'}, 502)

    @app.post('/api/{action}', openapi_extra={
        'requestBody': {
            'required': True,
            'content': {'application/json': {
                'schema': {'type': 'object', 'properties': {
                    'message': {'type': 'string', 'description': 'Required for chat (1–6,000 characters).'},
                    'state': {'type': 'string', 'description': 'Signed state returned by the previous request; required for image.'},
                }},
                'examples': {
                    'chat': {'value': {'message': 'Three days in Lisbon, interested in food'}},
                    'reset': {'value': {}},
                    'image': {'value': {'state': 'PASTE_PREVIOUS_RESPONSE_STATE'}},
                },
            }},
        },
    })
    async def api(action: Literal['chat', 'reset', 'image'], request: Request,
                  x_travel_request: str = Header(default='1', description='Must be 1.')):
        if action not in ('chat', 'reset', 'image'):
            return JSONResponse({'error': 'Unknown action.'}, 404)
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return JSONResponse({'error': 'Request body is too large.'}, 413)
            chunks.append(chunk)
        import json
        try:
            body = json.loads(b''.join(chunks))
        except (ValueError, UnicodeError):
            return JSONResponse({'error': 'Invalid request body.'}, 400)
        if not isinstance(body, dict):
            return JSONResponse({'error': 'Invalid request body.'}, 400)
        return await run_in_threadpool(process, action, body)

    return app


app = create_app()
if __name__ == '__main__':
    import uvicorn
    uvicorn.run('web.app:app', host='127.0.0.1', port=int(os.getenv('PORT', '8000')))
