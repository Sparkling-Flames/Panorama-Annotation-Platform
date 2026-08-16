from django.urls import path

from . import views

urlpatterns = [
    path("worker/activity-events", views.worker_activity_event_view, name="worker-activity-event"),
]
