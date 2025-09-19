from celery import Celery

# This is the single source of truth for the Celery application
celery = Celery(
    'tasks',
    broker='redis://redis:6379/0',
    backend='redis://redis:6379/0'
)

celery.conf.update(
    task_track_started=True,
    broker_connection_retry_on_startup=True
)

# --- FIX: Explicitly import the module that contains your tasks ---
import process_video
