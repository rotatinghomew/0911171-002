from .fieldwork import Fieldwork


def main() -> None:
    sample = [
        {"unit_id": "n1", "stratum": "north", "base_weight": 1},
        {"unit_id": "n2", "stratum": "north", "base_weight": 1},
        {"unit_id": "s1", "stratum": "south", "base_weight": 2},
    ]
    fieldwork = Fieldwork(sample)
    fieldwork.respond("n1", {"hours": 4})
    fieldwork.respond("s1", {"hours": 6})
    print(fieldwork.release({"north": 10, "south": 10}, "hours"))


if __name__ == "__main__":
    main()
