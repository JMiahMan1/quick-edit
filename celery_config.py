from celery import Celery

# This is the single source of truth for the Celery application
celery = Celery(
    'tasks',
    broker='redis://redis:6379/0',
    backend='redis://redis:6379/0'
)

celery.conf.update(
    task_track_started=True
)
