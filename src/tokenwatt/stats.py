"""Small stats toolkit. Pure Python so the tool installs with one dependency."""
import math


def mean(xs):
    return sum(xs) / len(xs) if xs else float("nan")


def stdev(xs):
    n = len(xs)
    if n < 2:
        return float("nan")
    m = mean(xs)
    return math.sqrt(sum((x - m) ** 2 for x in xs) / (n - 1))


def ranks(xs):
    """Average ranks (1-based), ties share the mean of their positions."""
    order = sorted(range(len(xs)), key=lambda i: xs[i])
    r = [0.0] * len(xs)
    i = 0
    while i < len(order):
        j = i
        while j + 1 < len(order) and xs[order[j + 1]] == xs[order[i]]:
            j += 1
        avg = (i + j) / 2 + 1
        for k in range(i, j + 1):
            r[order[k]] = avg
        i = j + 1
    return r


def pearson(xs, ys):
    n = len(xs)
    if n != len(ys) or n < 3:
        return float("nan")
    mx, my = mean(xs), mean(ys)
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs)
    syy = sum((y - my) ** 2 for y in ys)
    if sxx == 0 or syy == 0:
        return float("nan")
    return max(-1.0, min(1.0, sxy / math.sqrt(sxx * syy)))


def spearman(xs, ys):
    return pearson(ranks(xs), ranks(ys))


# --- Student t distribution, via the regularised incomplete beta function ---

def _betacf(a, b, x):
    tiny = 1e-300
    qab, qap, qam = a + b, a + 1.0, a - 1.0
    c, d = 1.0, 1.0 - qab * x / qap
    d = 1.0 / (d if abs(d) > tiny else tiny)
    h = d
    for m in range(1, 300):
        m2 = 2 * m
        aa = m * (b - m) * x / ((qam + m2) * (a + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        h *= d * c
        aa = -(a + m) * (qab + m) * x / ((a + m2) * (qap + m2))
        d = 1.0 + aa * d
        d = 1.0 / (d if abs(d) > tiny else tiny)
        c = 1.0 + aa / c
        c = c if abs(c) > tiny else tiny
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 3e-14:
            break
    return h


def betainc(a, b, x):
    if x <= 0:
        return 0.0
    if x >= 1:
        return 1.0
    lbt = (math.lgamma(a + b) - math.lgamma(a) - math.lgamma(b)
           + a * math.log(x) + b * math.log(1 - x))
    bt = math.exp(lbt)
    if x < (a + 1) / (a + b + 2):
        return bt * _betacf(a, b, x) / a
    return 1.0 - bt * _betacf(b, a, 1 - x) / b


def t_sf2(t, df):
    """Two-sided p-value for a t statistic."""
    if math.isnan(t) or df <= 0:
        return float("nan")
    if math.isinf(t):
        return 0.0
    return betainc(df / 2, 0.5, df / (df + t * t))


def corr_pvalue(r, n):
    if math.isnan(r) or n < 3:
        return float("nan")
    if abs(r) >= 1:
        return 0.0
    t = r * math.sqrt((n - 2) / (1 - r * r))
    return t_sf2(t, n - 2)


def linregress(xs, ys):
    """Ordinary least squares y = a + b x. Returns dict with slope, intercept, r2, slope_se, p."""
    n = len(xs)
    out = {"n": n, "slope": float("nan"), "intercept": float("nan"),
           "r2": float("nan"), "slope_se": float("nan"), "p": float("nan")}
    if n < 3:
        return out
    mx, my = mean(xs), mean(ys)
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx == 0:
        return out
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    b = sxy / sxx
    a = my - b * mx
    sse = sum((y - a - b * x) ** 2 for x, y in zip(xs, ys))
    sst = sum((y - my) ** 2 for y in ys)
    out.update(slope=b, intercept=a, r2=(1 - sse / sst) if sst else float("nan"))
    if n > 2:
        se = math.sqrt(sse / (n - 2) / sxx)
        out["slope_se"] = se
        if se > 0:
            out["p"] = t_sf2(b / se, n - 2)
        else:
            out["p"] = 0.0
    return out


def lstsq(rows, ys):
    """Multiple regression via normal equations (small k only). rows are feature lists, no intercept added."""
    k = len(rows[0])
    xtx = [[sum(r[i] * r[j] for r in rows) for j in range(k)] for i in range(k)]
    xty = [sum(r[i] * y for r, y in zip(rows, ys)) for i in range(k)]
    # Gauss-Jordan with partial pivoting
    m = [row[:] + [v] for row, v in zip(xtx, xty)]
    for col in range(k):
        piv = max(range(col, k), key=lambda r: abs(m[r][col]))
        if abs(m[piv][col]) < 1e-12:
            return None
        m[col], m[piv] = m[piv], m[col]
        div = m[col][col]
        m[col] = [v / div for v in m[col]]
        for r in range(k):
            if r != col and m[r][col] != 0:
                f = m[r][col]
                m[r] = [a - f * b for a, b in zip(m[r], m[col])]
    return [m[i][k] for i in range(k)]


def welch(a, b):
    """Welch's t-test. Returns (difference of means a-b, p-value)."""
    na, nb = len(a), len(b)
    if na < 2 or nb < 2:
        return float("nan"), float("nan")
    va, vb = stdev(a) ** 2, stdev(b) ** 2
    diff = mean(a) - mean(b)
    se2 = va / na + vb / nb
    if se2 == 0:
        return diff, (0.0 if diff else 1.0)
    t = diff / math.sqrt(se2)
    df = se2 ** 2 / ((va / na) ** 2 / (na - 1) + (vb / nb) ** 2 / (nb - 1))
    return diff, t_sf2(t, df)


def lag1_autocorr(xs):
    if len(xs) < 4:
        return float("nan")
    return pearson(xs[:-1], xs[1:])


def effective_n(n, x, y):
    """Bartlett-style correction: neighbouring time windows are not independent."""
    rx, ry = lag1_autocorr(x), lag1_autocorr(y)
    if math.isnan(rx) or math.isnan(ry):
        return n
    rho = rx * ry
    if rho <= 0:
        return n
    return max(3, int(n * (1 - rho) / (1 + rho)))
