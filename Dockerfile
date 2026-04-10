FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "alembic upgrade head && if [ -n \"$ADMIN_EMAIL\" ] && [ -n \"$ADMIN_PASSWORD\" ]; then python scripts/seed_admin.py --email \"$ADMIN_EMAIL\" --password \"$ADMIN_PASSWORD\" --create; fi && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
