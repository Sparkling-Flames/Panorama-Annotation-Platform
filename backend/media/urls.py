from django.urls import path

from . import views

urlpatterns = [
    path("admin/media/candidates", views.candidates_view, name="media-candidates"),
    path("admin/media/imports/preview", views.import_preview_view, name="media-import-preview"),
    path("admin/media/imports/publish", views.import_publish_view, name="media-import-publish"),
    path("admin/media/imports/cancel", views.import_cancel_view, name="media-import-cancel"),
]
