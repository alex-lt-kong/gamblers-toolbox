"""Host-level token->cookie auth gate, shared by every module.

Disabled when no auth_tokens are configured. When enabled:
  - `?token=<uuid>` matching a configured token sets a signed session cookie
    and redirects to the same URL without the token;
  - requests carrying the cookie pass; everything else gets 401.
"""

from typing import Awaitable, Callable, Iterable

from starlette.requests import Request
from starlette.responses import PlainTextResponse, RedirectResponse, Response

_Next = Callable[[Request], Awaitable[Response]]


def make_auth_gate(tokens: Iterable[str]) -> Callable[[Request, _Next], Awaitable[Response]]:
    valid = {t for t in tokens if t}

    async def gate(request: Request, call_next: _Next) -> Response:
        if not valid:
            return await call_next(request)
        supplied = request.query_params.get("token")
        if supplied and supplied in valid:
            request.session["authed"] = True
            return RedirectResponse(str(request.url.remove_query_params("token")), status_code=303)
        if request.session.get("authed"):
            return await call_next(request)
        return PlainTextResponse("Unauthorized", status_code=401)

    return gate
