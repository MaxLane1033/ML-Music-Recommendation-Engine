from pathlib import Path

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DB_PATH = Path(__file__).resolve().parent.parent / "vibes.db"

engine = create_engine(
    f"sqlite:///{DB_PATH}",
    connect_args={"check_same_thread": False},
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)


class Base(DeclarativeBase):
    pass


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def run_lightweight_migrations() -> None:
    """Add columns introduced after a table already existed, in place.

    There's no real migration system (see project notes), and blowing away
    vibes.db on every schema tweak would destroy the user's saved vibes. For
    the rare case of a genuinely new column on an existing table, just
    ALTER TABLE it in -- idempotent, so it's a no-op on fresh databases
    (where create_all already created the column) and on databases that
    already have it.
    """
    inspector = inspect(engine)
    if "recommended_songs" not in inspector.get_table_names():
        return
    columns = {col["name"] for col in inspector.get_columns("recommended_songs")}
    if "user_rank" not in columns:
        with engine.begin() as conn:
            conn.execute(text("ALTER TABLE recommended_songs ADD COLUMN user_rank INTEGER"))
