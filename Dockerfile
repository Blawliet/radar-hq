# Imagem oficial do Playwright já traz Chromium e dependências do sistema
FROM mcr.microsoft.com/playwright/python:v1.55.0-noble

WORKDIR /app
ENV PYTHONUNBUFFERED=1 TZ=America/Sao_Paulo

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt && playwright install chromium

COPY src ./src
COPY config ./config

CMD ["python", "src/main.py", "--agendar"]
