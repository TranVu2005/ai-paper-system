from sqlalchemy.orm import Session
from app.models.refresh_token import RefreshToken


def create_refresh_token(db: Session, token: str, user_email: str, device_id: str):
    db_token = RefreshToken(
        token=token,
        user_email=user_email,
        device_id=device_id,
        revoked=False
    )
    db.add(db_token)
    db.commit()
    db.refresh(db_token)
    return db_token


def revoke_refresh_token(db: Session, token: str):
    db_token = db.query(RefreshToken).filter(
        RefreshToken.token == token,
        RefreshToken.revoked == False
    ).first()

    if db_token:
        db_token.revoked = True
        db.commit()

    return db_token


def is_refresh_token_revoked(db: Session, token: str) -> bool:
    db_token = db.query(RefreshToken).filter(
        RefreshToken.token == token
    ).first()

    if not db_token:
        return True

    return db_token.revoked
def rotate_refresh_token(
    db: Session,
    old_token: str,
    new_token: str,
    user_email: str,
    device_id: str
) -> bool:
    old = (
        db.query(RefreshToken)
        .filter(
            RefreshToken.token == old_token,
            RefreshToken.revoked == False
        )
        .first()
    )

    if not old:
        return False

    # revoke token cũ
    old.revoked = True

    # lưu token mới
    db.add(
        RefreshToken(
            token=new_token,
            user_email=user_email,
            device_id=device_id,
            revoked=False
        )
    )

    db.commit()
    return True