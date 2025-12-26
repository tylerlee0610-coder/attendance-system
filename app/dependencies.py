from fastapi import HTTPException, Request, status


def _raise_redirect(location: str):
    raise HTTPException(
        status_code=status.HTTP_302_FOUND,
        headers={"Location": location},
    )


def require_role(required_role: str):
    """Dependency factory enforcing session login and specific role."""

    def dependency(request: Request):
        session = request.session or {}
        user_id = session.get("user_id")
        role = session.get("role")
        if not user_id:
            _raise_redirect("/login?error=login_required")
        if role != required_role:
            _raise_redirect("/login?error=forbidden")
        return {"user_id": user_id, "role": role}

    return dependency


def require_roles(allowed_roles: set[str]):
    """Dependency enforcing session login and membership in allowed roles."""

    def dependency(request: Request):
        session = request.session or {}
        user_id = session.get("user_id")
        role = session.get("role")
        if not user_id:
            _raise_redirect("/login?error=login_required")
        if role not in allowed_roles:
            _raise_redirect("/login?error=forbidden")
        return {"user_id": user_id, "role": role}

    return dependency
