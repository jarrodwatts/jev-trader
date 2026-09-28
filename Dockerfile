FROM python:3.12-slim
WORKDIR /app
ENV PYTHONUNBUFFERED=1 HOST=0.0.0.0
COPY pyproject.toml ./
COPY jev_trader ./jev_trader
RUN pip install --no-cache-dir ".[fast]"
EXPOSE 3000
CMD ["python", "-m", "jev_trader"]
