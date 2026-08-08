import os

# Local tests must not depend on the Docker Compose hostname `db`.
os.environ.setdefault("DATABASE_URL", "sqlite+aiosqlite:///:memory:")
