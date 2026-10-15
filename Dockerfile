FROM python:3.12-slim
WORKDIR /app
COPY requirements-streamlit.txt ./requirements.txt
RUN pip install --no-cache-dir -r requirements.txt
COPY advisor.py itinerary_prompt.txt streamlit_app.py streamlit_style.html pdf_export.py ./
COPY web/static/exploration.svg ./web/static/exploration.svg
COPY .streamlit/config.toml ./.streamlit/config.toml
EXPOSE 8501
CMD ["python", "-m", "streamlit", "run", "streamlit_app.py", "--server.address=0.0.0.0", "--server.port=8501"]
