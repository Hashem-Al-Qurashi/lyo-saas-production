import logging
from contextlib import contextmanager
from psycopg2.pool import ThreadedConnectionPool
from app.config import settings

logger = logging.getLogger(__name__)

_pool: ThreadedConnectionPool | None = None


def get_pool() -> ThreadedConnectionPool:
    global _pool
    if _pool is None or _pool.closed:
        _pool = ThreadedConnectionPool(
            minconn=2,
            maxconn=10,
            host=settings.db_host,
            port=settings.db_port,
            database=settings.db_name,
            user=settings.db_user,
            password=settings.db_password,
            sslmode=settings.db_sslmode,
        )
        logger.info("Database connection pool created")
    return _pool


@contextmanager
def get_connection():
    pool = get_pool()
    conn = pool.getconn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        pool.putconn(conn)


def close_pool():
    global _pool
    if _pool and not _pool.closed:
        _pool.closeall()
        logger.info("Database connection pool closed")
