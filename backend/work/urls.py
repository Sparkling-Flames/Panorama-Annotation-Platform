from django.urls import path

from . import views

urlpatterns = [
    path("admin/work-batches", views.admin_work_batches_view, name="admin-work-batches"),
    path(
        "admin/work-batches/<uuid:batch_id>/assignments",
        views.admin_batch_assignments_view,
        name="admin-batch-assignments",
    ),
    path("worker/batches", views.worker_batches_view, name="worker-batches"),
    path(
        "worker/batches/<uuid:batch_id>/assignments",
        views.worker_batch_assignments_view,
        name="worker-batch-assignments",
    ),
    path(
        "worker/assignments/<uuid:assignment_id>",
        views.worker_assignment_view,
        name="worker-assignment",
    ),
    path(
        "worker/assignments/<uuid:assignment_id>/open",
        views.worker_assignment_open_view,
        name="worker-assignment-open",
    ),
    path(
        "worker/assignments/<uuid:assignment_id>/queue-state",
        views.worker_assignment_queue_state_view,
        name="worker-assignment-queue-state",
    ),
]
