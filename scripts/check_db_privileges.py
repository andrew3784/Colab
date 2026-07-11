from sqlalchemy import text

from flood_analysis.db import get_engine


def main() -> None:
    query = text(
        """
        SELECT
            current_database() AS database,
            current_user AS username,
            current_schema() AS schema,
            has_database_privilege(current_database(), 'CREATE') AS can_create_database_objects,
            has_schema_privilege('public', 'CREATE') AS can_create_public,
            has_schema_privilege('public', 'USAGE') AS can_use_public
        """
    )
    with get_engine().connect() as conn:
        row = conn.execute(query).mappings().one()
    for key, value in row.items():
        print(f"{key}={value}")


if __name__ == "__main__":
    main()
