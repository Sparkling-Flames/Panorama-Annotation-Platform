from django.urls import path

from . import views

urlpatterns = [
    path("worker/activity-events", views.worker_activity_event_view, name="worker-activity-event"),
    path(
        "worker/assignments/<uuid:assignment_id>/activity-summary",
        views.worker_activity_summary_view,
        name="worker-activity-summary",
    ),
]
