"""Authentication (bcrypt passwords + signed session cookie) and role checks."""
import bcrypt
from fastapi import HTTPException, Request

from app import db


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def check_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode(), password_hash.encode())
    except ValueError:
        return False


def login(request: Request, email: str, password: str):
    """Returns the session user dict on success, None on wrong email/password."""
    row = db.get_user_by_email(email)
    if row is None or not check_password(password, row["password_hash"]):
        return None
    user = {"user_id": row["user_id"], "name": row["full_name"],
            "role": row["role"], "domain_id": row["domain_id"]}
    request.session["user"] = user
    return user


def register_author(full_name: str, email: str, password: str) -> int:
    """Public sign-up always creates an author. Reviewers/admins are created by an admin."""
    if len(password) < 6:
        raise ValueError("Password must be at least 6 characters")
    return db.create_user(full_name, email, hash_password(password), "author")


def logout(request: Request):
    request.session.clear()


def current_user(request: Request):
    return request.session.get("user")


def require(*roles):
    """FastAPI dependency: the user must be logged in and have one of the given roles."""
    def checker(request: Request):
        user = current_user(request)
        if user is None:
            raise HTTPException(status_code=401, detail="Not logged in")
        if roles and user["role"] not in roles:
            raise HTTPException(status_code=403, detail="Your role cannot do this")
        return user
    return checker
