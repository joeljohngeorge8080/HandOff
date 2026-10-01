from handoff.files.naming import unique_display_name


def test_unused_name_is_kept():
    assert unique_display_name("photo.jpg", []) == "photo.jpg"


def test_collision_gets_numbered_without_space():
    assert unique_display_name("photo.jpg", ["photo.jpg"]) == "photo(1).jpg"


def test_numbering_continues_until_unique():
    existing = ["photo.jpg", "photo(1).jpg", "photo(2).jpg"]
    assert unique_display_name("photo.jpg", existing) == "photo(3).jpg"


def test_gap_in_numbering_is_reused_first_free_slot():
    assert unique_display_name("photo.jpg", ["photo.jpg", "photo(2).jpg"]) == "photo(1).jpg"


def test_collision_is_case_insensitive():
    assert unique_display_name("PHOTO.JPG", ["photo.jpg"]) == "PHOTO(1).JPG"


def test_extension_is_preserved():
    assert unique_display_name("a.b.txt", ["a.b.txt"]) == "a.b(1).txt"


def test_input_that_already_looks_numbered_is_still_made_unique():
    assert unique_display_name("photo(1).jpg", ["photo(1).jpg"]) == "photo(1)(1).jpg"
