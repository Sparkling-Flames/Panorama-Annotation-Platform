from django.urls import path

from . import views

urlpatterns = [
    path("auth/csrf", views.csrf_view, name="csrf"),
    path("auth/session", views.session_view, name="session"),
    path("auth/login", views.login_view, name="login"),
    path("auth/change-password", views.change_password_view, name="change-password"),
    path("workspace/session", views.workspace_session_view, name="workspace-session"),
    path("workspace/acquire", views.acquire_workspace_view, name="workspace-acquire"),
    path("workspace/renew", views.renew_workspace_view, name="workspace-renew"),
    path("admin/workers", views.workers_collection_view, name="workers-collection"),
    path("admin/workers/<str:worker_id>", views.worker_detail_view, name="worker-detail"),
    path(
        "admin/workers/<str:worker_id>/reset-password",
        views.reset_password_view,
        name="worker-reset-password",
    ),
    path(
        "admin/workers/<str:worker_id>/revoke-sessions",
        views.revoke_sessions_view,
        name="worker-revoke-sessions",
    ),
    path(
        "admin/workers/<str:worker_id>/disable",
        views.disable_worker_view,
        name="worker-disable",
    ),
    path(
        "admin/workers/<str:worker_id>/restore",
        views.restore_worker_view,
        name="worker-restore",
    ),
]
