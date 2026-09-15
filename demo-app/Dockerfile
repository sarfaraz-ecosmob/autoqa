FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

RUN groupadd -r demo && useradd -r -g demo -d /app demo

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY . .
RUN chown -R demo:demo /app

USER demo

EXPOSE 9000

HEALTHCHECK --interval=15s --timeout=5s --retries=5 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:9000/health', timeout=4).status==200 else 1)"

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "9000"]
