"""Administrative connection for disposable examples; never migrate this DB."""
import getpass
import os

from sqlalchemy import create_engine
from sqlalchemy.engine import URL, make_url


def admin_engine(source):
    if source.host not in ("localhost", "127.0.0.1", "::1"):
        raise ValueError("Disposable examples require a loopback application database")
    raw = os.environ.get("TEST_DATABASE_ADMIN_URL")
    if raw:
        admin = make_url(raw)
        if (admin.host not in ("localhost", "127.0.0.1", "::1")
                or admin.database not in ("postgres", "template1")
                or admin.get_backend_name() != "postgresql"
                or admin.query):
            raise ValueError("TEST_DATABASE_ADMIN_URL must name a loopback postgres/template1 database without options")
    else:
        admin = URL.create("postgresql+psycopg", username=getpass.getuser(),
                           port=source.port, database="postgres")
    return create_engine(admin.set(drivername="postgresql+psycopg"), isolation_level="AUTOCOMMIT")
