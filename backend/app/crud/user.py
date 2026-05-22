from sqlalchemy.orm import Session

from app.models.user import User
from app.schemas.user import UserCreate
from app.core.security import get_password_hash, verify_password


# =========================
# GET USER
# =========================

def get_user_by_email(db: Session, email: str):
    return db.query(User).filter(User.email == email.lower().strip()).first()


def get_user_by_id(db: Session, user_id: int):
    return db.query(User).filter(User.id == user_id).first()


# =========================
# CREATE USER (REGISTER)
# =========================

def create_user(db: Session, user_in: UserCreate):
    hashed_pw = get_password_hash(user_in.password)

    user = User(
        email=user_in.email.lower().strip(),
        hashed_password=hashed_pw,
        full_name=user_in.full_name
    )

    db.add(user)
    db.commit()
    db.refresh(user)

    return user


# =========================
# AUTHENTICATE USER (LOGIN)
# =========================

def authenticate_user(db: Session, email: str, password: str):
    user = get_user_by_email(db, email)

    if not user:
        return None

    if not verify_password(password, user.hashed_password):
        return None

    return user
