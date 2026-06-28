FROM python:3.13-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

WORKDIR /app

# Install system deps for psycopg
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev gcc \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN DATABASE_URL="sqlite:///tmp/build.db" python manage.py collectstatic --noinput

EXPOSE ${PORT:-10000}

CMD python manage.py migrate --noinput && python manage.py seed_notification_templates --quiet && gunicorn plfog.wsgi:application --bind 0.0.0.0:${PORT:-10000} --workers 2
