"""Stateless FastAPI interface; run with python -m web.app."""
import base64
import hmac
import os
import re
import copy
import json
import logging
import time
import uuid
from pathlib import Path
from typing import Literal

from dotenv import load_dotenv
from fastapi import FastAPI, Header, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from itsdangerous import BadData, URLSafeTimedSerializer
from starlette.concurrency import run_in_threadpool

from safarai.advisor import TravelAdvisor, safe_markdown
from safarai.errors import describe_failure

logger = logging.getLogger('safarai.requests')

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

    def process(action, body):
        try:
            signer = serializer()
        except RuntimeError as exc:
            return JSONResponse({'error': str(exc)}, 503)
        if action != 'restore' and not advisor_factory and not os.getenv('OPENAI_API_KEY'):
            return JSONResponse({'error': 'Configure OPENAI_API_KEY on the server.'}, 503)
        advisor = advisor_factory() if advisor_factory else TravelAdvisor(
            os.getenv('OPENAI_API_KEY', ''), budget_usd=float(os.getenv('CONVERSATION_BUDGET_USD', '1')))
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
        previous_state = copy.deepcopy({field: getattr(advisor, field) for field in STATE_FIELDS if field != 'metrics'})
        request_id = uuid.uuid4().hex
        advisor.request_id = request_id
        started = time.perf_counter()
        try:
            if action == 'restore':
                result = {'itinerary': advisor.last_itinerary, 'itinerary_html': safe_markdown(advisor.last_itinerary) if advisor.last_itinerary else None,
                          'destination': (advisor.last_brief or {}).get('destination'),
                          'messages': [{'role': m['role'], 'html': safe_markdown(
                              'Your adventure plan is ready! Tell me what you would like to change.'
                              if m['role'] == 'assistant' and re.search(r'(?im)^#{1,6}\s+Day\s+\d+|^## Estimated budget', m['content'])
                              else m['content'])} for m in advisor.history if m['role'] in ('user', 'assistant')]}
            elif action == 'reset':
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
        except Exception as exc:
            # Roll back conversation/plan mutations, but retain accrued usage.
            for field, value in previous_state.items():
                setattr(advisor, field, value)
            failure = describe_failure(exc)
            logger.error(json.dumps({'event': 'travel_request_failed', 'request_id': request_id,
                'action': action, 'stage': getattr(advisor, 'stage', action), 'error_code': failure.code,
                'exception_type': type(exc).__name__, 'provider_status': getattr(failure, 'provider_status', None),
                'context_upper_bound': getattr(advisor, 'last_context_bound', None),
                'provider_request_id': getattr(failure, 'provider_request_id', None),
                'elapsed_ms': round((time.perf_counter()-started)*1000)}))
            has_plan = bool(advisor.last_itinerary)
            message = str(failure) + (' Your previous itinerary is still available.' if has_plan else '')
            return JSONResponse({'error': message, 'code': failure.code, 'retryable': failure.retryable,
                'request_id': request_id, 'has_itinerary': has_plan,
                'usage_html': advisor.usage_summary_html(),
                'state': signer.dumps({field: getattr(advisor, field) for field in STATE_FIELDS})},
                failure.status, headers={'X-Request-ID': request_id})

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
    async def api(action: Literal['chat', 'reset', 'image', 'restore'], request: Request,
                  x_travel_request: str = Header(default='1', description='Must be 1.')):
        size = 0
        chunks = []
        async for chunk in request.stream():
            size += len(chunk)
            if size > MAX_BODY:
                return JSONResponse({'error': 'Request body is too large.'}, 413)
            chunks.append(chunk)
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
