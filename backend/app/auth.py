import bcrypt
from fastapi import Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import User


class NotAuthenticated(Exception):
    pass


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()


def verify_password(password: str, password_hash: str) -> bool:
    return bcrypt.checkpw(password.encode(), password_hash.encode())


def any_user_exists(db: Session) -> bool:
    return db.scalar(select(User.id).limit(1)) is not None


def get_user_by_username(db: Session, username: str) -> User | None:
    return db.scalar(select(User).where(User.username == username))


def require_login(request: Request) -> dict:
    user = request.session.get("user")
    if not user:
        raise NotAuthenticated()
    return user


def current_user_or_none(request: Request) -> dict | None:
    return request.session.get("user")


def require_admin(user: dict = Depends(require_login)) -> dict:
    if user.get("role") != "admin":
        raise HTTPException(status_code=403, detail="Action réservée aux administrateurs")
    return user
