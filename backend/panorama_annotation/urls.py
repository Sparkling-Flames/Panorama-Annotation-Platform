from django.http import HttpRequest, JsonResponse
from django.urls import include, path


def health(_request: HttpRequest) -> JsonResponse:
    return JsonResponse({"status": "ok"})


urlpatterns = [
    path("api/health", health, name="health"),
    path("api/", include("identity.urls")),
    path("api/", include("media.urls")),
]
