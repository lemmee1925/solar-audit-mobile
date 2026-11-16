from pathlib import Path
from sqlalchemy import create_mock_engine
from models import Base

output = Path("postgres_schema_reference.sql")
statements = []

def collect(sql, *multiparams, **params):
    statements.append(
        str(sql.compile(dialect=engine.dialect)).strip() + ";"
    )

engine = create_mock_engine(
    "postgresql+psycopg://",
    collect
)

Base.metadata.create_all(engine)

output.write_text(
    "-- Generated from current SQLAlchemy models\n"
    "-- Reference only; not executed against any database.\n\n"
    + "\n\n".join(statements)
    + "\n",
    encoding="utf-8"
)

print("Tables:", len(Base.metadata.tables))
print("DDL statements:", len(statements))
print("Generated:", output.resolve())
