release: python backend/manage.py migrate --noinput
web: gunicorn --chdir backend --bind 0.0.0.0:${PORT:-8000} panorama_annotation.wsgi:application
worker: python backend/manage.py run_worker
