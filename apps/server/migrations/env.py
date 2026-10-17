from alembic import context
from workbench.composition import tables  # noqa: F401
from workbench.infrastructure.database import Base, engine

target_metadata = Base.metadata


def include_object(obj, name, type_, reflected, compare_to):
    # SDK checkpoint tables and unrelated database objects have separate ownership.
    # Removing an application table requires an explicit reviewed migration.
    return not (type_ == "table" and reflected and name not in target_metadata.tables)


connection = context.config.attributes.get("connection")
if connection is not None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        include_object=include_object,
    )
    with context.begin_transaction():
        context.run_migrations()
else:
    with engine().connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )
        with context.begin_transaction():
            context.run_migrations()
