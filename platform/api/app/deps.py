from dataclasses import dataclass
from fastapi import Depends, HTTPException, Request, status

from .repositories import Repository
from .supabase_client import get_supabase_admin


@dataclass
class AuthContext:
    user_id: str
    org_id: str
    role: str


def _extract_bearer(request: Request) -> str:
    auth = request.headers.get("Authorization", "")
    if not auth.lower().startswith("bearer "):
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing bearer token")
    return auth.split(" ", 1)[1].strip()


def get_auth_context(request: Request, repo: Repository = Depends(Repository)) -> AuthContext:
    token = _extract_bearer(request)
    sb = get_supabase_admin()
    user = sb.auth.get_user(token)
    if not user or not user.user:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")

    profile = repo.get_profile(user.user.id)
    if not profile:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Profile not provisioned")

    return AuthContext(user_id=profile["id"], org_id=profile["org_id"], role=profile["role"])


def require_operator_or_admin(ctx: AuthContext = Depends(get_auth_context)) -> AuthContext:
    if ctx.role not in {"admin", "operator"}:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail="Operator role required")
    return ctx
