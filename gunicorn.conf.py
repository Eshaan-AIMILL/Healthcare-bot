import os
import multiprocessing

# Bind
bind = os.getenv("GUNICORN_BIND", "0.0.0.0:8000")

# Workers
workers = int(os.getenv("GUNICORN_WORKERS", min(multiprocessing.cpu_count() * 2 + 1, 4)))
worker_class = "uvicorn.workers.UvicornWorker"

# Timeouts
timeout = int(os.getenv("GUNICORN_TIMEOUT", 120))  # LLM calls can be slow
graceful_timeout = 30
keepalive = 5

# Logging
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()

# Process naming
proc_name = "healthcare-bot"

# Preload app for faster worker startup (shared memory for model loading)
preload_app = True
