"""SafarAI web app. Start with: python -m web.app."""
import base64
import hmac
import os
import secrets
import threading
import time
from pathlib import Path

from dotenv import load_dotenv
from flask import Flask, jsonify, render_template, request
from werkzeug.middleware.proxy_fix import ProxyFix

from advisor import TravelAdvisor, safe_markdown

ROOT = Path(__file__).resolve().parents[1]
load_dotenv(ROOT / '.env')


def create_app(advisor_factory=None):
    app = Flask(__name__)
    # Enable only behind the hosting provider's single trusted HTTPS proxy.
    if os.getenv('TRUST_PROXY', 'false').lower() == 'true':
        app.wsgi_app = ProxyFix(app.wsgi_app, x_for=0, x_proto=1, x_host=0, x_port=0, x_prefix=0)
    app.config['TEMPLATES_AUTO_RELOAD'] = True
    app.config['MAX_CONTENT_LENGTH'] = 16000
    sessions = {}
    session_lock = threading.Lock()
    secure = os.getenv('COOKIE_SECURE', 'false').lower() == 'true'

    # Protect private deployments and reject cross-site requests.
    @app.before_request
    def protect():
        if request.path == '/health':
            return None
        password = os.getenv('APP_PASSWORD', '')
        if password:
            auth = request.authorization
            if not auth or not hmac.compare_digest(auth.password or '', password):
                return 'Sign in to your travel advisor.', 401, {'WWW-Authenticate': 'Basic realm="Travel Advisor"'}
        if request.path.startswith('/api/') and request.method == 'POST':
            if request.headers.get('X-Travel-Request') != '1':
                return jsonify(error='Invalid request.'), 403
            origin = request.headers.get('Origin')
            if origin and origin != request.host_url.rstrip('/'):
                return jsonify(error='Cross-site requests are blocked.'), 403

    @app.after_request
    def headers(response):
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Referrer-Policy'] = 'same-origin'
        response.headers['Content-Security-Policy'] = "default-src 'self'; img-src 'self' data:; style-src 'self'; script-src 'self'; frame-ancestors 'none'; base-uri 'self'"
        return response

    # Each browser gets its own advisor; inactive sessions expire after an hour.
    def get_session():
        now = time.monotonic()
        sid = request.cookies.get('travel_session')
        with session_lock:
            for key, value in list(sessions.items()):
                if now - value['seen'] > 3600 and not value['lock'].locked():
                    del sessions[key]
            if sid not in sessions:
                if len(sessions) >= 100:
                    raise RuntimeError('Session capacity reached. Try again later.')
                sid = secrets.token_urlsafe(32)
                advisor = advisor_factory() if advisor_factory else TravelAdvisor(
                    os.getenv('OPENAI_API_KEY', ''),
                    budget_usd=float(os.getenv('CONVERSATION_BUDGET_USD', '1')),
                )
                sessions[sid] = {'advisor': advisor, 'lock': threading.Lock(), 'seen': now, 'image': None}
            sessions[sid]['seen'] = now
            return sid, sessions[sid]

    @app.get('/')
    def home():
        return render_template('index.html', configured=bool(os.getenv('OPENAI_API_KEY')))

    @app.get('/health')
    def health():
        return jsonify(status='ok')

    # Three actions: send a message, clear the trip, or request one image.
    @app.post('/api/<action>')
    def api(action):
        if action not in ('chat', 'reset', 'image'):
            return jsonify(error='Unknown action.'), 404
        if not advisor_factory and not os.getenv('OPENAI_API_KEY'):
            return jsonify(error='Add OPENAI_API_KEY to the server environment, then restart the app.'), 503
        try:
            sid, state = get_session()
        except RuntimeError as exc:
            return jsonify(error=str(exc)), 503
        if not state['lock'].acquire(blocking=False):
            return jsonify(error='Your previous request is still running.'), 409
        try:
            advisor = state['advisor']
            if action == 'reset':
                advisor.reset()
                state['image'] = None
                result = {'reset': True}
            elif action == 'image':
                if not advisor.last_itinerary:
                    return jsonify(error='Create an itinerary first.'), 400
                if state['image'] is None:
                    state['image'] = base64.b64encode(advisor.generate_image()).decode('ascii')
                result = {'image': 'data:image/png;base64,' + state['image']}
            else:
                body = request.get_json(silent=True) or {}
                if not isinstance(body, dict):
                    return jsonify(error='Invalid request body.'), 400
                text = body.get('message')
                if not isinstance(text, str) or not text.strip() or len(text) > 6000:
                    return jsonify(error='Enter a message of 1–6,000 characters.'), 400
                previous_plan, previous_brief = advisor.last_itinerary, advisor.last_brief
                try:
                    reply = advisor.reply(text)
                except Exception:
                    advisor.last_itinerary, advisor.last_brief = previous_plan, previous_brief
                    raise
                new_plan = advisor.last_itinerary
                result = {'reply': safe_markdown(reply), 'itinerary': new_plan,
                          'itinerary_html': safe_markdown(new_plan) if new_plan else None,
                          'destination': (advisor.last_brief or {}).get('destination')}
                if new_plan:
                    state['image'] = None
                else:
                    advisor.last_itinerary, advisor.last_brief = previous_plan, previous_brief
            result['usage_html'] = advisor.usage_summary_html()
            response = jsonify(result)
            response.set_cookie('travel_session', sid, httponly=True, secure=secure, samesite='Strict', max_age=3600)
            return response
        except (RuntimeError, ValueError, KeyError, TypeError):
            return jsonify(error='The request failed. Check API access or quota, or try a shorter request. Your itinerary remains available.'), 502
        finally:
            state['seen'] = time.monotonic()
            state['lock'].release()

    return app


app = create_app()
if __name__ == '__main__':
    app.run(host='127.0.0.1', port=int(os.getenv('PORT', '8000')), debug=False)
