"""SafarAI on Streamlit: python -m streamlit run streamlit_app.py."""
import hmac
import os
from pathlib import Path

import streamlit as st
from dotenv import load_dotenv

from advisor import TravelAdvisor, safe_markdown
from pdf_export import itinerary_pdf

ROOT = Path(__file__).resolve().parent
load_dotenv(ROOT / '.env')
st.set_page_config(page_title='SafarAI', page_icon='✈️', layout='wide')
st.markdown((ROOT / 'streamlit_style.html').read_text(encoding='utf-8'), unsafe_allow_html=True)


def setting(name, default=''):
    """Use Cloud secrets when configured, or local environment variables."""
    try:
        return st.secrets.get(name, os.getenv(name, default))
    except FileNotFoundError:
        return os.getenv(name, default)


def new_trip():
    st.session_state.advisor.reset()
    st.session_state.chat = []
    st.session_state.plan = None
    st.session_state.image = None


# Optional password protects the API account when sharing a private demo.
password = setting('APP_PASSWORD')
if password and not st.session_state.get('signed_in'):
    st.title('Welcome to SafarAI ✈️')
    with st.form('sign_in'):
        entered = st.text_input('Your app password', type='password')
        if st.form_submit_button('Start exploring'):
            if hmac.compare_digest(entered, password):
                st.session_state.signed_in = True
                st.rerun()
            else:
                st.error('Incorrect password.')
    st.stop()

api_key = setting('OPENAI_API_KEY')
if not api_key:
    st.title('SafarAI ✈️')
    st.info('Add OPENAI_API_KEY in Streamlit Cloud → App settings → Secrets, or in your local .env file.')
    st.stop()

# Session state is private to this browser session; no shared conversation cache.
if 'advisor' not in st.session_state:
    st.session_state.advisor = TravelAdvisor(api_key, budget_usd=float(setting('CONVERSATION_BUDGET_USD', '1')))
    new_trip()
advisor = st.session_state.advisor

title, reset = st.columns([4, 1])
with title:
    st.title('SafarAI ✈️')
    st.caption('Small dreams. Big adventures!')
with reset:
    st.button('＋ New trip', on_click=new_trip, width='stretch')

intro, art = st.columns([3, 2])
with intro:
    st.header('Big adventures. Happy little plans.')
    st.write('Pack your curiosity! Tell us where you want to go and what makes you smile.')
    st.caption('☀ Dream it · ✈ Explore it · ♡ Make memories')
with art:
    st.image(str(ROOT / 'web/static/exploration.svg'), width='stretch')

chat_panel, plan_panel = st.columns([1, 1.4], gap='large')
with chat_panel:
    st.subheader('Meet your adventure buddy')
    with st.container(height=380):
        with st.chat_message('assistant'):
            st.write('Hi, explorer! Where would you like to go, for how long, and what do you enjoy doing?')
        for role, text in st.session_state.chat:
            with st.chat_message(role):
                if role == 'user':
                    st.text(text)
                else:
                    st.markdown(safe_markdown(text), unsafe_allow_html=True)

    prompt = st.chat_input('Describe your dream trip…', max_chars=6000)
    if prompt:
        previous_plan, previous_brief = advisor.last_itinerary, advisor.last_brief
        try:
            with st.spinner('Finding your way…'):
                reply = advisor.reply(prompt)
            st.session_state.chat.append(('user', prompt))
            if advisor.last_itinerary:
                st.session_state.plan = advisor.last_itinerary
                st.session_state.image = None
                reply = 'Your adventure plan is ready! Tell me what you would like to change.'
            else:
                advisor.last_itinerary, advisor.last_brief = previous_plan, previous_brief
            st.session_state.chat.append(('assistant', reply))
            st.rerun()
        except (RuntimeError, ValueError, KeyError, TypeError):
            advisor.last_itinerary, advisor.last_brief = previous_plan, previous_brief
            st.error('The request failed. Check API access or quota, or try a shorter request.')

with plan_panel:
    st.subheader('Your adventure map')
    plan = st.session_state.plan
    if not plan:
        st.info('🌍 Ready, set… let’s explore! Your itinerary will appear here.')
    else:
        st.download_button('↓ Download PDF', itinerary_pdf(plan, st.session_state.image),
                           file_name='safarai-travel-plan.pdf', mime='application/pdf')
        if st.button('✧ Add a destination image', disabled=st.session_state.image is not None):
            try:
                with st.spinner('Creating a little inspiration…'):
                    st.session_state.image = advisor.generate_image()
                st.rerun()
            except (RuntimeError, ValueError):
                st.error('Image generation failed. Your itinerary is still available.')
        st.caption('Images are optional and use the low-quality setting. They are generated only on request.')
        if st.session_state.image:
            st.image(st.session_state.image, caption='AI-generated illustration, not a map.', width='stretch')
        st.markdown(safe_markdown(plan), unsafe_allow_html=True)

with st.expander('Conversation usage & cost'):
    st.markdown(advisor.usage_summary_html(), unsafe_allow_html=True)
st.caption('Check prices and local details before booking. OpenAI requests use your API account.')
