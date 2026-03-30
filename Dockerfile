FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY db/ db/
COPY seed_harvester/ seed_harvester/
COPY persona_forge/ persona_forge/
COPY negotiation_runner/ negotiation_runner/
COPY prediction_synthesiser/ prediction_synthesiser/
COPY augur_api.py pipeline.py asx200.py ./

EXPOSE 8000

CMD ["sh", "-c", "uvicorn augur_api:app --host 0.0.0.0 --port ${PORT:-8000}"]
