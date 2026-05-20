"""
Courbe des taux et pricing oblig (style modèle Excel).

- MM : simple ACT/360  =>  1 + r * d/360
- Actuariel : DF = (1+R)^(-t), t = d/365
- Conversion : (1 + r*d/360) = (1+R)^t  =>  R = (1 + r*d/360)^(1/t) - 1
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


def year_fraction_act365(days: float | np.ndarray) -> float | np.ndarray:
    return np.asarray(days, dtype=float) / 365.0


def linear_interp(x, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    x = np.asarray(x, dtype=float)
    xp = np.asarray(xp, dtype=float)
    fp = np.asarray(fp, dtype=float)
    order = np.argsort(xp)
    xp, fp = xp[order], fp[order]
    return np.interp(x, xp, fp, left=fp[0], right=fp[-1])


def linear_interp_extrapolate(x, xp: np.ndarray, fp: np.ndarray) -> np.ndarray:
    """
    Interpolation linéaire sur [xp[0], xp[-1]] ; en dehors, extrapolation sur le premier / dernier segment.
    Reproduit souvent Excel au-delà du dernier pilier « utile » lorsque _mat2 est tronqué avant un point cap.
    """
    x = np.asarray(x, dtype=float)
    xp = np.asarray(xp, dtype=float)
    fp = np.asarray(fp, dtype=float)
    if xp.size == 0:
        return np.full_like(x, np.nan, dtype=float)
    order = np.argsort(xp)
    xp, fp = xp[order], fp[order]
    if xp.size == 1:
        return np.full_like(x, fp[0], dtype=float)
    out = np.interp(x, xp, fp)
    left = x < xp[0]
    slope_lo = (fp[1] - fp[0]) / (xp[1] - xp[0])
    out = np.where(left, fp[0] + slope_lo * (x - xp[0]), out)
    right = x > xp[-1]
    slope_hi = (fp[-1] - fp[-2]) / (xp[-1] - xp[-2])
    out = np.where(right, fp[-1] + slope_hi * (x - xp[-1]), out)
    return out


def convert_to_actuarial(days: float | np.ndarray, mm_rate: float | np.ndarray) -> np.ndarray:
    """mm_rate en décimal (ex. 0.0227)."""
    d = np.asarray(days, dtype=float)
    r = np.asarray(mm_rate, dtype=float)
    t = year_fraction_act365(d)
    growth = 1.0 + r * d / 360.0
    R = np.power(growth, 1.0 / np.maximum(t, 1e-12)) - 1.0
    return np.where(d > 0, R, 0.0)


def discount_factor(actuarial_rate: float | np.ndarray, days: float | np.ndarray) -> np.ndarray:
    t = year_fraction_act365(days)
    R = np.asarray(actuarial_rate, dtype=float)
    return np.power(1.0 + R, -t)


def zc_rate_continuous(df: float | np.ndarray, days: float | np.ndarray) -> np.ndarray:
    t = year_fraction_act365(days)
    df = np.asarray(df, dtype=float)
    return np.where(t > 0, -np.log(df) / t, 0.0)


def zc_rate_annual_effective(df: float | np.ndarray, days: float | np.ndarray) -> np.ndarray:
    t = year_fraction_act365(days)
    df = np.asarray(df, dtype=float)
    return np.where(t > 0, np.power(df, -1.0 / t) - 1.0, 0.0)


@dataclass
class CurveInputs:
    short_maturities_days: np.ndarray
    short_mm_rates: np.ndarray
    long_maturities_days: np.ndarray
    long_actuarial_rates: np.ndarray
    # Dernier jour strictement « court » au sens Excel = MAX(_mat1) − 1 (ex. 325 si dernier CT = 326 j).
    # ``YieldCurve.mm_cutoff_day`` est toujours dérivé de max(short), indépendamment de cette valeur.
    joint_days: float = 325.0


class YieldCurve:
    """
    Segment CT : interpolation linéaire MM sur ``_mat1`` jusqu'à ``mm_cutoff_day`` = max(short days).

    Au-delà de 365 j : interpolation linéaire sur les piliers longs (taux actuariels).

    Entre ``mm_cutoff_day`` et 365 j : rampe monétaire Excel (même construction que l'échéancier annuel).
    """

    def __init__(self, inputs: CurveInputs):
        s_d = np.asarray(inputs.short_maturities_days, dtype=float)
        s_r = np.asarray(inputs.short_mm_rates, dtype=float)
        l_d = np.asarray(inputs.long_maturities_days, dtype=float)
        l_R = np.asarray(inputs.long_actuarial_rates, dtype=float)
        o_s, o_l = np.argsort(s_d), np.argsort(l_d)
        self._s_d, self._s_r = s_d[o_s], s_r[o_s]
        self._l_d, self._l_R = l_d[o_l], l_R[o_l]
        self.joint = float(inputs.joint_days)
        # Dernier jour du segment CT monétaire = MAX(_mat1) côté Excel (ex. G2 = 326, 255, 192 selon la date BAM).
        self.mm_cutoff_day = float(np.max(self._s_d)) if self._s_d.size > 0 else self.joint + 1.0

    def _mm_at(self, days):
        return linear_interp(np.asarray(days, dtype=float), self._s_d, self._s_r)

    def money_market_rate(self, days):
        """Interpolation sur la courbe CT monétaire (_mat1/taux1)."""
        d = np.atleast_1d(np.asarray(days, dtype=float))
        out = np.asarray(self._mm_at(d), dtype=float)
        return out[0] if out.size == 1 else out

    def _actuarial_long_at(self, days):
        return linear_interp(np.asarray(days, dtype=float), self._l_d, self._l_R)

    def long_actuarial_rate(self, days):
        """Interpolation uniquement sur la courbe MLT (équivalent Excel: interpoler(_mat2;taux2;K))."""
        d = np.atleast_1d(np.asarray(days, dtype=float))
        out = np.asarray(self._actuarial_long_at(d), dtype=float)
        return out[0] if out.size == 1 else out

    def long_actuarial_rate_for_schedule(self, days, schedule_max_maturity_days: float):
        """
        MLT pour le tableau « Échéancier annuel » : on ne garde que les piliers LT
        dont la maturité ≤ la plus grande ligne du tableau (ex. 10 957 j). Les points
        cap au-delà (ex. 10 958 j) sont exclus, puis extrapolation linéaire après le
        dernier pilier retenu — aligné sur Excel pour la dernière maturité d’échéancier.
        """
        d = np.atleast_1d(np.asarray(days, dtype=float))
        cap = float(schedule_max_maturity_days)
        m = self._l_d <= cap
        if np.sum(m) >= 2:
            ld = self._l_d[m]
            lr = self._l_R[m]
            out = linear_interp_extrapolate(d, ld, lr)
        else:
            out = np.asarray(self._actuarial_long_at(d), dtype=float)
        out = np.asarray(out, dtype=float)
        return out[0] if out.size == 1 else out

    def actuarial_rate(self, days):
        d = np.atleast_1d(np.asarray(days, dtype=float))
        out = np.empty_like(d, dtype=float)
        m_le_year = d <= 365.0
        m_gt_year = d > 365.0
        if np.any(m_le_year):
            b = self._quoted_schedule_like(d[m_le_year])
            out[m_le_year] = convert_to_actuarial(d[m_le_year], b)
        if np.any(m_gt_year):
            out[m_gt_year] = self._actuarial_long_at(d[m_gt_year])
        return out[0] if out.size == 1 else out

    def quoted_rate(self, days):
        d = np.atleast_1d(np.asarray(days, dtype=float))
        out = np.empty_like(d, dtype=float)
        m_le_year = d <= 365.0
        m_gt_year = d > 365.0
        if np.any(m_le_year):
            out[m_le_year] = self._quoted_schedule_like(d[m_le_year])
        if np.any(m_gt_year):
            out[m_gt_year] = self._actuarial_long_at(d[m_gt_year])
        return out[0] if out.size == 1 else out

    def _quoted_schedule_like(self, days: np.ndarray) -> np.ndarray:
        """
        Taux « coté Excel » pour M ≤ 365 j : MM pur jusqu'à ``mm_cutoff_day`` (= MAX _mat1),
        puis rampe monétaire entre (cutoff, MM au cutoff) et (1er pilier LT, MM synthétique).
        """
        d = np.asarray(days, dtype=float)
        out = np.empty_like(d, dtype=float)
        cut = float(self.mm_cutoff_day)
        mask_ct = d <= cut
        mask_trans = (d > cut) & (d <= 365.0)
        out[mask_ct] = self._mm_at(d[mask_ct])
        if np.any(mask_trans):
            d_trans = d[mask_trans]
            mm_last_short = float(self._mm_at(np.array([cut], dtype=float))[0])
            l_d = self._l_d
            l_r = self._l_R
            first_long_d_arr = l_d[l_d > cut] if l_d.size > 0 else np.array([], dtype=float)
            if first_long_d_arr.size > 0:
                first_long_d = float(first_long_d_arr[0])
                first_long_r = float(l_r[l_d > cut][0])
                mm_first_long_synth = (
                    np.power(1.0 + first_long_r, first_long_d / 365.0) - 1.0
                ) * 360.0 / first_long_d
                slope = (mm_first_long_synth - mm_last_short) / (first_long_d - cut)
                out[mask_trans] = mm_last_short + slope * (d_trans - cut)
            else:
                r_trans = self._actuarial_long_at(d_trans)
                out[mask_trans] = (
                    (np.power(1.0 + r_trans, d_trans / 365.0) - 1.0) * 360.0 / d_trans
                )
        return out

    def build_table(self, maturity_days: np.ndarray) -> pd.DataFrame:
        d = np.asarray(maturity_days, dtype=float)
        t = year_fraction_act365(d)
        R = np.asarray(self.actuarial_rate(d), dtype=float)
        q = np.asarray(self.quoted_rate(d), dtype=float)
        df = discount_factor(R, d)
        return pd.DataFrame(
            {
                "Maturity_days": d,
                "Year_fraction": t,
                "Rate": q,
                "ZC_rate_continuous": zc_rate_continuous(df, d),
                "ZC_rate_annual_effective": zc_rate_annual_effective(df, d),
                "Discount_factor": df,
                "Actuarial_rate": R,
            }
        )


def interpolate_rate(
    days: float | np.ndarray,
    curve: YieldCurve,
    *,
    kind: Literal["actuarial", "quoted"] = "actuarial",
):
    return curve.actuarial_rate(days) if kind == "actuarial" else curve.quoted_rate(days)


def _coupon_schedule(maturity_days: float, frequency: int, settlement_days: float = 0.0):
    freq = int(frequency)
    if freq < 1:
        raise ValueError("frequency >= 1")
    mats = maturity_days - settlement_days
    if mats <= 0:
        return np.array([]), np.array([])
    dt = 365.0 / freq
    n_full = int(np.floor(mats / dt))
    rem = mats - n_full * dt
    days_list = []
    if rem > 1e-9:
        days_list.append(rem)
    for k in range(1, n_full + 1):
        days_list.append(rem + k * dt)
    days_list = np.asarray(sorted(days_list), dtype=float)
    return days_list, year_fraction_act365(days_list)


def price_bond(
    curve: YieldCurve,
    nominal: float,
    coupon_rate: float,
    maturity_days: float,
    frequency: int,
    settlement_days: float = 0.0,
) -> tuple[float, pd.DataFrame]:
    pay_d, pay_t = _coupon_schedule(maturity_days, frequency, settlement_days)
    if pay_d.size == 0:
        return 0.0, pd.DataFrame()

    cpn = nominal * coupon_rate / frequency
    cfs = np.full_like(pay_d, cpn, dtype=float)
    cfs[-1] += nominal

    R = np.asarray(curve.actuarial_rate(pay_d), dtype=float)
    dfs = discount_factor(R, pay_d)
    pvs = cfs * dfs

    cf_df = pd.DataFrame(
        {
            "Payment_day": pay_d,
            "Year_fraction": pay_t,
            "Cash_flow": cfs,
            "Actuarial_rate": R,
            "Discount_factor": dfs,
            "PV": pvs,
        }
    )
    return float(pvs.sum()), cf_df


def _ytm_bisection(cfs: np.ndarray, times: np.ndarray, price_target: float) -> float:
    def px(y: float) -> float:
        return float(np.sum(cfs / np.power(1.0 + y, times)))

    lo, hi = -0.99, 0.5
    for _ in range(80):
        if px(hi) < price_target:
            hi += 0.5
        else:
            break
    fa, fb = px(lo) - price_target, px(hi) - price_target
    if fa * fb > 0:
        raise ValueError("YTM : impossible de trouver un encadrement.")
    a, b = (lo, hi) if fa < 0 else (hi, lo)
    fa, fb = px(a) - price_target, px(b) - price_target
    for _ in range(200):
        m = 0.5 * (a + b)
        fm = px(m) - price_target
        if abs(fm) < 1e-10:
            return m
        if fa * fm <= 0:
            b, fb = m, fm
        else:
            a, fa = m, fm
    return 0.5 * (a + b)


def duration(
    cfs: np.ndarray,
    times: np.ndarray,
    spot_rates: np.ndarray,
    *,
    ytm: float | None = None,
    modified: bool = False,
) -> float:
    dfs = np.power(1.0 + spot_rates, -times)
    pvs = cfs * dfs
    p = pvs.sum()
    if p == 0:
        return 0.0
    d_mac = float(np.sum(times * pvs) / p)
    if not modified:
        return d_mac
    if ytm is None:
        raise ValueError("ytm requis pour la duration modifiée.")
    return d_mac / (1.0 + ytm)


def convexity(cfs: np.ndarray, times: np.ndarray, ytm: float) -> float:
    y = ytm
    terms = times * (times + 1.0) * cfs / np.power(1.0 + y, times + 2.0)
    p = np.sum(cfs / np.power(1.0 + y, times))
    return float(terms.sum() / p) if p != 0 else 0.0


def dv01_parallel_curve(
    curve: YieldCurve,
    nominal: float,
    coupon_rate: float,
    maturity_days: float,
    frequency: int,
    bump_bp: float = 1.0,
    settlement_days: float = 0.0,
) -> float:
    class Shifted:
        def __init__(self, c: YieldCurve, s: float):
            self._c, self._s = c, s

        def actuarial_rate(self, days):
            return np.asarray(self._c.actuarial_rate(days), dtype=float) + self._s

    bp = bump_bp * 1e-4
    p0, _ = price_bond(curve, nominal, coupon_rate, maturity_days, frequency, settlement_days)
    p1, _ = price_bond(Shifted(curve, bp), nominal, coupon_rate, maturity_days, frequency, settlement_days)
    return float(p1 - p0)


def bond_valuation_report(
    curve: YieldCurve,
    nominal: float,
    coupon_rate: float,
    maturity_days: float,
    frequency: int,
    settlement_days: float = 0.0,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    dirty, cf_df = price_bond(curve, nominal, coupon_rate, maturity_days, frequency, settlement_days)
    if cf_df.empty:
        return cf_df, pd.DataFrame({"Metric": ["Dirty_price"], "Value": [dirty]})

    cfs = cf_df["Cash_flow"].to_numpy()
    times = cf_df["Year_fraction"].to_numpy()
    R_spot = cf_df["Actuarial_rate"].to_numpy()

    ytm = _ytm_bisection(cfs, times, dirty)
    d_mac = duration(cfs, times, R_spot, modified=False)
    d_mod = duration(cfs, times, R_spot, modified=True, ytm=ytm)
    cx = convexity(cfs, times, ytm)
    dv01 = dv01_parallel_curve(
        curve, nominal, coupon_rate, maturity_days, frequency, settlement_days=settlement_days
    )

    summary = pd.DataFrame(
        {
            "Metric": [
                "Dirty_price",
                "YTM_actuarial",
                "Macaulay_duration_years",
                "Modified_duration",
                "Convexity",
                "DV01_parallel_1bp",
            ],
            "Value": [dirty, ytm, d_mac, d_mod, cx, dv01],
        }
    )
    return cf_df, summary
