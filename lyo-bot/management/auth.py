from datetime import datetime, timedelta
from typing import Optional
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.config import settings
from app.models.database import get_connection

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)


def hash_password(password: str) -> str:
    return pwd_context.hash(password)


def create_token(email: str, business_id: int, role: str = "owner") -> str:
    expire = datetime.utcnow() + timedelta(minutes=settings.jwt_expire_minutes)
    return jwt.encode(
        {"sub": email, "business_id": business_id, "role": role, "exp": expire},
        settings.jwt_secret, algorithm=settings.jwt_algorithm,
    )


def decode_token(token: str) -> Optional[dict]:
    try:
        return jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        return None


def authenticate_user(email: str, password: str) -> Optional[dict]:
    with get_connection() as conn:
        cur = conn.cursor()
        cur.execute(
            "SELECT id, business_id, email, password_hash, name, role FROM management_users WHERE email = %s AND is_active = true",
            (email,),
        )
        row = cur.fetchone()
        if not row or not verify_password(password, row[3]):
            return None
        return {"id": row[0], "business_id": row[1], "email": row[2], "name": row[4], "role": row[5]}
