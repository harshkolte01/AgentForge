"""Bound API request bytes while streaming, before JSON/model buffering."""
from starlette.exceptions import HTTPException
from starlette.responses import JSONResponse

class RequestBodyLimitMiddleware:
    def __init__(self, app, maximum=1_000_000):
        self.app, self.maximum = app, maximum

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not scope.get("path", "").startswith("/api/"):
            return await self.app(scope, receive, send)
        lengths = [value for key, value in scope.get("headers", []) if key.lower() == b"content-length"]
        if lengths and (len(lengths) != 1 or len(lengths[0]) > 20 or not lengths[0].isdigit() or int(lengths[0]) > self.maximum):
            return await JSONResponse({"detail": "request_body_size_limit"}, status_code=413)(scope, receive, send)
        received = 0
        async def bounded_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.maximum:
                    raise HTTPException(413, "request_body_size_limit")
            return message
        await self.app(scope, bounded_receive, send)
