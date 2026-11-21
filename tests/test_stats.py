import math

import pytest

from tokenwatt import stats


def test_pearson_perfect():
    assert stats.pearson([1, 2, 3, 4], [2, 4, 6, 8]) == pytest.approx(1.0)
    assert stats.pearson([1, 2, 3, 4], [8, 6, 4, 2]) == pytest.approx(-1.0)


def test_pearson_constant_is_nan():
    assert math.isnan(stats.pearson([1, 1, 1, 1], [1, 2, 3, 4]))


def test_ranks_with_ties():
    assert stats.ranks([10, 20, 20, 30]) == [1.0, 2.5, 2.5, 4.0]


def test_spearman_monotonic_nonlinear():
    xs = list(range(1, 20))
    assert stats.spearman(xs, [x ** 3 for x in xs]) == pytest.approx(1.0)


def test_t_pvalues_match_tables():
    # t = 2.228 at df = 10 is the 5% two-sided critical value
    assert stats.t_sf2(2.228, 10) == pytest.approx(0.05, abs=5e-4)
    # t = 1.96 with huge df approaches the normal 5%
    assert stats.t_sf2(1.96, 100000) == pytest.approx(0.05, abs=5e-4)
    assert stats.t_sf2(0.0, 7) == pytest.approx(1.0)


def test_corr_pvalue():
    # r = 0.632 at n = 10 is right on p = 0.05
    assert stats.corr_pvalue(0.632, 10) == pytest.approx(0.05, abs=2e-3)


def test_linregress_recovers_line():
    xs = [0, 1, 2, 3, 4, 5]
    ys = [3 + 2 * x for x in xs]
    fit = stats.linregress(xs, ys)
    assert fit["slope"] == pytest.approx(2)
    assert fit["intercept"] == pytest.approx(3)
    assert fit["r2"] == pytest.approx(1)


def test_lstsq_two_features():
    rows = [[1, x, z] for x, z in [(0, 1), (1, 0), (2, 3), (3, 1), (4, 4), (5, 2)]]
    ys = [1 + 2 * r[1] - 0.5 * r[2] for r in rows]
    coef = stats.lstsq(rows, ys)
    assert coef == pytest.approx([1, 2, -0.5])


def test_welch():
    diff, p = stats.welch([10, 11, 12, 11, 10, 12], [5, 6, 5, 6, 5, 6])
    assert diff == pytest.approx(5.5)
    assert p < 0.001
    _, p_same = stats.welch([1, 2, 3, 4], [1, 2, 3, 4])
    assert p_same == pytest.approx(1.0)


def test_effective_n_shrinks_for_smooth_series():
    xs = [math.sin(i / 10) for i in range(200)]
    ys = [math.cos(i / 10) for i in range(200)]
    assert stats.effective_n(200, xs, ys) < 50
