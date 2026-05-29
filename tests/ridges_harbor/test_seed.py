from ridges_harbor.seed import MAX_INFERENCE_SEED, problem_seed


def test_problem_seed_is_deterministic_and_in_range() -> None:
    seed = problem_seed("django__django-11119")

    assert seed == problem_seed("django__django-11119")
    assert 0 <= seed <= MAX_INFERENCE_SEED


def test_problem_seed_known_values() -> None:
    assert problem_seed("django__django-11119") == 23423618
    assert problem_seed("astropy__astropy-7166") == 745136449
    assert problem_seed("polyglot_python_grade-school") == 185503529
