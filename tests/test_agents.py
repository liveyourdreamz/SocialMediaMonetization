def test_numerals_ignore_sentence_punctuation_and_thousands_separators():
    """'launched in 2024.' is the numeral 2024; the first regex kept the full stop, so
    any sentence ending in a number parked the post as an uncited numeral."""
    from cap.agents import _numerals

    assert _numerals("Launched in 2024. Costs $4.99. Ranked No. 1. Over 4,000 runs.") == [
        "2024",
        "4.99",
        "1",
        "4000",
    ]
