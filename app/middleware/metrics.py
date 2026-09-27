"""Counts and times every HTTP request by method, route template and status code.

The route is the template (`/api/v1/items/{external_id}`), never the concrete path, so the
number of label values stays bounded. Requests that match no route are labelled `unmatched`.
"""

import time

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.metrics import HTTP_REQUEST_DURATION, HTTP_REQUESTS

_METHODS = frozenset({"GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"})


class MetricsMiddleware:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        started = time.perf_counter()
        status = 500  # if the app raises before responding, the server answers 500

        async def send_with_status(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
            await send(message)

        try:
            await self.app(scope, receive, send_with_status)
        finally:
            method = scope["method"] if scope["method"] in _METHODS else "OTHER"
            route = route_template(scope)
            HTTP_REQUEST_DURATION.labels(method, route).observe(time.perf_counter() - started)
            HTTP_REQUESTS.labels(method, route, str(status)).inc()


def route_template(scope: Scope) -> str:
    """The full template of the route the router matched, e.g. `/api/v1/items/{external_id}`.

    The router stores the matched route in the scope. Recent FastAPI versions resolve included
    routers lazily and store the route with its path relative to the router's prefix
    (`/items/{external_id}`), so the prefix is taken from the request path: it is everything
    before the segments the template accounts for.
    """
    template = getattr(scope.get("route"), "path", None)
    if not isinstance(template, str):
        return "unmatched"
    if ":path}" in template:  # a parameter spanning several segments: segments do not line up
        return template
    path: str = scope["path"]
    root_path: str = scope.get("root_path", "")
    if root_path and path.startswith(root_path):
        path = path[len(root_path) :]
    path_segments = path.rstrip("/").split("/")
    template_segments = template.rstrip("/").split("/")
    prefix_length = len(path_segments) - len(template_segments) + 1
    prefix = "/".join(path_segments[:prefix_length]) if prefix_length > 1 else ""
    return prefix + template
