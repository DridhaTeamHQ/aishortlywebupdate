from fastapi import APIRouter, Depends

from ..deps import AuthContext, require_operator_or_admin
from ..models import SaveSecretsRequest
from ..repositories import Repository

router = APIRouter(prefix="/api/secrets", tags=["secrets"])


@router.post("/me")
def save_my_secrets(
    payload: SaveSecretsRequest,
    ctx: AuthContext = Depends(require_operator_or_admin),
    repo: Repository = Depends(Repository),
):
    repo.save_user_secrets(
        ctx.user_id,
        ctx.org_id,
        {
            "CMS_URL": payload.cms_url,
            "CMS_EMAIL": payload.cms_email,
            "CMS_PASSWORD": payload.cms_password,
            "OPENAI_API_KEY": payload.openai_api_key,
        },
    )
    return {"saved": True}
