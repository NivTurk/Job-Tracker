"""Request checks for a single-user app bound to localhost.

Binding to 127.0.0.1 keeps other machines out, but a web page open in the
same browser could still send requests to it. Two checks cover that:
- TrustedHostMiddleware (set up in main.py) rejects unexpected Host headers,
  which blocks DNS-rebinding tricks.
- SameOriginMiddleware rejects state-changing requests whose Origin header
  points at another site (cross-site form posts).
"""

from urllib.parse import urlparse

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


class SameOriginMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
        if request.method not in SAFE_METHODS:
            origin = request.headers.get("origin")
            # "null" comes from sandboxed frames and file:// pages, so it is refused too.
            if origin and urlparse(origin).netloc != request.headers.get("host", ""):
                return PlainTextResponse("Cross-origin request refused.", status_code=403)
        return await call_next(request)
