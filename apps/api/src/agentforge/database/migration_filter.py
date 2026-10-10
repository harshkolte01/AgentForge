"""Keep separately managed LangGraph tables out of application autogeneration."""

CHECKPOINT_TABLES = frozenset({"checkpoint_migrations", "checkpoints", "checkpoint_blobs", "checkpoint_writes"})


def include_autogenerate_object(obj, name, type_, reflected, compare_to):
    # Only unmanaged reflected tables are excluded. Unknown tables, application
    # metadata and every other object remain visible to Alembic drift detection.
    return not (type_ == "table" and reflected and compare_to is None and name in CHECKPOINT_TABLES)
