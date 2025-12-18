from fastapi import HTTPException, Request, status


def require_role(required_role: str):
    """Dependency factory enforcing session login and specific role."""

    def dependency(request: Request):
        session = request.session or {}
        user_id = session.get("user_id")
        role = session.get("role")
        if not user_id:
            raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                headers={"Location": "/login?error=login_required"},
            )
        if role != required_role:
            raise HTTPException(
                status_code=status.HTTP_302_FOUND,
                headers={"Location": "/login?error=forbidden"},
            )
        return {"user_id": user_id, "role": role}

    return dependency
