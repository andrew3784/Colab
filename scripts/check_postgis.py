from flood_analysis.db import check_postgis, get_engine


def main() -> None:
    version = check_postgis(get_engine())
    print(version)


if __name__ == "__main__":
    main()
