"""
SenteFlow AI — Firebase Auth Dependency
Set REQUIRE_AUTH=false in .env to skip in local dev.
"""
import os
import logging
from fastapi import Depends, Header, HTTPException

logger = logging.getLogger(__name__)
# Safety guard: REQUIRE_AUTH=false is only permitted when ENVIRONMENT != production.
# This prevents a dev convenience flag from accidentally disabling auth in prod.
_env = os.environ.get("ENVIRONMENT", "production").lower()
_auth_override = os.environ.get("REQUIRE_AUTH", "true").lower() == "false"
_REQUIRE_AUTH = True  # default: always enforce
if _auth_override:
    if _env in ("production", "prod"):
        import warnings
        warnings.warn(
            "REQUIRE_AUTH=false is set but ENVIRONMENT=production — auth will be ENFORCED. "
            "Set ENVIRONMENT=development to allow auth bypass.",
            stacklevel=1,
        )
    else:
        _REQUIRE_AUTH = False


async def verify_firebase_token(authorization: str = Header(default=None)) -> dict:
    if not _REQUIRE_AUTH:
        return {"uid": "dev-user", "email": "dev@local"}
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed Authorization header")
    token = authorization.split(" ", 1)[1]
    try:
        import firebase_admin.auth as fb_auth
        return fb_auth.verify_id_token(token)
    except Exception as exc:
        logger.warning("token_verification_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=401, detail="Invalid or expired token")


async def verify_org_access(org_id: str, token: dict = Depends(verify_firebase_token)) -> dict:
    if not _REQUIRE_AUTH:
        return token
    uid = token.get("uid")
    if not uid:
        raise HTTPException(status_code=403, detail="Token missing uid")
    try:
        import firebase_admin.firestore as fs
        doc = fs.client().collection("organizations").document(org_id).collection("members").document(uid).get()
        if not doc.exists:
            logger.warning("org_access_denied", extra={"uid": uid, "org_id": org_id})
            raise HTTPException(status_code=403, detail="Access denied to this organization")
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("org_access_check_failed", extra={"error": str(exc)})
        raise HTTPException(status_code=500, detail="Could not verify org access")
    return token