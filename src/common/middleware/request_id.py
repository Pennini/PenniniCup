from uuid import UUID, uuid4

from src.common.utils.request_id import clear_request_id, set_request_id


class RequestUUIDMiddleware:
    header_name = "X-Request-UUID"
    meta_key = "HTTP_X_REQUEST_UUID"

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        request_id = self._resolve_request_id(request)
        request.request_id = request_id
        request.META[self.meta_key] = request_id
        set_request_id(request_id)

        try:
            response = self.get_response(request)
        finally:
            clear_request_id()

        response[self.header_name] = request_id
        return response

    def _resolve_request_id(self, request) -> str:
        incoming = (request.META.get(self.meta_key) or "").strip()
        if incoming:
            try:
                return str(UUID(incoming))
            except ValueError:
                pass
        return str(uuid4())
