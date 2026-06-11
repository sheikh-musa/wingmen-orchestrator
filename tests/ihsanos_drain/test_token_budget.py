from ihsanos_drain.token_budget import within_budget


def test_within_budget_true_when_under_cap():
    assert within_budget(spent_today=1000, cap=200_000) is True


def test_within_budget_false_when_at_or_over_cap():
    assert within_budget(spent_today=200_000, cap=200_000) is False
    assert within_budget(spent_today=250_000, cap=200_000) is False


def test_within_budget_true_when_cap_none():
    assert within_budget(spent_today=999_999, cap=None) is True
