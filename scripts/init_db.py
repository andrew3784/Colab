from flood_analysis.db import get_engine, init_db


def main() -> None:
    init_db(get_engine())
    print("Initialized PostGIS schemas and tables.")


if __name__ == "__main__":
    main()
