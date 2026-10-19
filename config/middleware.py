import zoneinfo
from urllib.parse import unquote

from django.utils import timezone

_COOKIE = 'timezone'
_FALLBACK = zoneinfo.ZoneInfo('America/New_York')


class TimezoneMiddleware:
    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith('/admin/'):
            return self.get_response(request)
        timezone.activate(_zone(request.COOKIES.get(_COOKIE)))
        try:
            return self.get_response(request)
        finally:
            timezone.deactivate()


def _zone(name: str | None) -> zoneinfo.ZoneInfo:
    if not name:
        return _FALLBACK
    try:
        return zoneinfo.ZoneInfo(unquote(name))
    except zoneinfo.ZoneInfoNotFoundError, ValueError:
        return _FALLBACK
