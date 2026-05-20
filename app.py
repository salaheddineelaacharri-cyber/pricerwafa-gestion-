"""
Dashboard Streamlit — Courbe des taux & pricing oblig (style institutionnel).
"""

from __future__ import annotations

import io

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from backend.main import FIXED_BENCHMARK_DAYS, excel_style_maturity_order, maturity_grid
from yield_curve import CurveInputs, YieldCurve, bond_valuation_report

# --- Style Wafa Gestion / AM institutionnel ---
COLORS = {
    "primary": "#0B3A82",
    "primary_light": "#0E4B99",
    "accent": "#0D9488",
    "accent_muted": "#14B8A6",
    "text": "#1A2B3C",
    "muted": "#64748B",
    "border": "#E2E8F0",
    "card": "#FFFFFF",
    "page": "#F0F2F5",
    "grid": "rgba(15, 23, 42, 0.08)",
}

STYLES = f"""
<style>
    /* Header bar */
    .wg-header {{
        background: linear-gradient(135deg, {COLORS["primary"]} 0%, {COLORS["primary_light"]} 100%);
        padding: 1.1rem 1.5rem;
        border-radius: 10px;
        margin-bottom: 1.25rem;
        box-shadow: 0 4px 14px rgba(11, 58, 130, 0.22);
    }}
    .wg-header h1 {{
        color: #FFFFFF !important;
        font-size: 1.45rem !important;
        font-weight: 600 !important;
        margin: 0 !important;
        letter-spacing: -0.02em;
    }}
    .wg-header p {{
        color: rgba(255,255,255,0.88) !important;
        margin: 0.35rem 0 0 0 !important;
        font-size: 0.9rem !important;
    }}
    /* Section cards */
    .wg-card {{
        background: {COLORS["card"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 10px;
        padding: 1rem 1.15rem;
        margin-bottom: 1rem;
        box-shadow: 0 1px 3px rgba(15, 23, 42, 0.06);
    }}
    .wg-card-title {{
        color: {COLORS["primary"]};
        font-size: 0.95rem;
        font-weight: 600;
        margin-bottom: 0.75rem;
        border-bottom: 2px solid {COLORS["accent"]};
        padding-bottom: 0.4rem;
        display: inline-block;
    }}
    /* Metric pills */
    div[data-testid="stMetric"] {{
        background: {COLORS["card"]};
        border: 1px solid {COLORS["border"]};
        border-radius: 8px;
        padding: 0.5rem;
    }}
    /* Sidebar tweaks */
    section[data-testid="stSidebar"] {{
        background: linear-gradient(180deg, #FFFFFF 0%, #F8FAFC 100%);
        border-right: 1px solid {COLORS["border"]};
    }}
    section[data-testid="stSidebar"] .stMarkdown strong {{
        color: {COLORS["primary"]};
    }}
</style>
"""


def inject_styles():
    st.markdown(STYLES, unsafe_allow_html=True)


def default_short_df() -> pd.DataFrame:
    """CT : MM (tableau Maturité CT / Taux CT)."""
    return pd.DataFrame(
        {
            "Maturity_days": [1, 53, 144, 326, 543],
            "MM_rate_pct": [2.27, 2.27, 2.34, 2.46, 2.57058253],
        }
    )


def default_long_df() -> pd.DataFrame:
    """LT : actuariel (tableau Maturité MLT / Taux MLT)."""
    return pd.DataFrame(
        {
            "Maturity_days": [326, 543, 1481, 1845, 3371, 4862, 7081, 10616, 10958],
            "Actuarial_rate_pct": [2.497469, 2.59, 2.98, 3.08, 3.28, 3.51, 3.65, 4.08, 4.122],
        }
    )


def parse_excel_curve(uploaded) -> tuple[pd.DataFrame, pd.DataFrame] | None:
    try:
        raw = uploaded.read()
        xls = pd.ExcelFile(io.BytesIO(raw))
        short_df, long_df = None, None
        for name in xls.sheet_names:
            low = name.strip().lower()
            df = pd.read_excel(io.BytesIO(raw), sheet_name=name)
            cols = [str(c).strip().lower() for c in df.columns]
            if "ct" in low or "court" in low or "short" in low:
                short_df = _normalize_two_col(df, mm=True)
            elif "lt" in low or "long" in low:
                long_df = _normalize_two_col(df, mm=False)
        if short_df is None and long_df is None and len(xls.sheet_names) >= 1:
            df0 = pd.read_excel(io.BytesIO(raw), sheet_name=0)
            short_df = _normalize_two_col(df0, mm=True)
        if short_df is None:
            short_df = default_short_df()
        if long_df is None:
            long_df = default_long_df()
        return short_df, long_df
    except Exception:
        return None


def _normalize_two_col(df: pd.DataFrame, mm: bool) -> pd.DataFrame:
    if df.shape[1] < 2:
        return default_short_df() if mm else default_long_df()
    c0, c1 = df.columns[0], df.columns[1]
    out = pd.DataFrame({df.columns[0]: df[c0], df.columns[1]: df[c1]}).dropna()
    out.columns = ["Maturity_days", "MM_rate_pct" if mm else "Actuarial_rate_pct"]
    out["Maturity_days"] = pd.to_numeric(out["Maturity_days"], errors="coerce")
    rate_col = "MM_rate_pct" if mm else "Actuarial_rate_pct"
    out[rate_col] = pd.to_numeric(
        out[rate_col].astype(str).str.replace(",", ".", regex=False), errors="coerce"
    )
    return out.dropna()


def build_curve_from_edited(short_df: pd.DataFrame, long_df: pd.DataFrame, joint_days: float) -> YieldCurve:
    s = short_df.dropna()
    l = long_df.dropna()
    mm_col = "MM_rate_pct" if "MM_rate_pct" in s.columns else s.columns[1]
    lt_col = "Actuarial_rate_pct" if "Actuarial_rate_pct" in l.columns else l.columns[1]
    inp = CurveInputs(
        short_maturities_days=s["Maturity_days"].to_numpy(dtype=float),
        short_mm_rates=s[mm_col].to_numpy(dtype=float) / 100.0,
        long_maturities_days=l["Maturity_days"].to_numpy(dtype=float),
        long_actuarial_rates=l[lt_col].to_numpy(dtype=float) / 100.0,
        joint_days=float(joint_days),
    )
    return YieldCurve(inp)


def plot_curve(table: pd.DataFrame) -> go.Figure:
    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=table["Maturity_days"],
            y=table["Actuarial_rate"] * 100.0,
            mode="lines",
            name="Taux actuariel",
            line=dict(color=COLORS["primary_light"], width=2.5),
            hovertemplate="Maturité: %{x} j<br>Taux: %{y:.4f}%<extra></extra>",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=table["Maturity_days"],
            y=table["Rate"] * 100.0,
            mode="lines",
            name="Taux côté marché (MM / LT)",
            line=dict(color=COLORS["accent"], width=1.8, dash="dot"),
            hovertemplate="Maturité: %{x} j<br>Quote: %{y:.4f}%<extra></extra>",
        )
    )
    fig.update_layout(
        title=dict(text="Courbe des taux", font=dict(size=18, color=COLORS["text"])),
        paper_bgcolor=COLORS["card"],
        plot_bgcolor=COLORS["page"],
        font=dict(family="system-ui, sans-serif", color=COLORS["text"], size=12),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=48, r=24, t=56, b=48),
        hovermode="x unified",
        xaxis=dict(
            title="Maturité (jours)",
            gridcolor=COLORS["grid"],
            zeroline=False,
            showline=True,
            linecolor=COLORS["border"],
        ),
        yaxis=dict(
            title="Taux (%)",
            gridcolor=COLORS["grid"],
            zeroline=False,
            showline=True,
            linecolor=COLORS["border"],
            ticksuffix="",
        ),
        height=420,
    )
    return fig


def main():
    st.set_page_config(
        page_title="Courbe des taux — Pricer",
        page_icon="📈",
        layout="wide",
        initial_sidebar_state="expanded",
    )
    inject_styles()

    st.markdown(
        f"""
        <div class="wg-header">
            <h1>Courbe des taux & obligations</h1>
            <p>Construction de courbe (CT / LT) · Actualisation · Pricing — tableau de bord professionnel</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    if "short_df" not in st.session_state:
        st.session_state.short_df = default_short_df()
    if "long_df" not in st.session_state:
        st.session_state.long_df = default_long_df()

    with st.sidebar:
        st.markdown("**Paramètres courbe**")
        joint_days = st.number_input(
            "Seuil joint (jours) — 325 = LT actuariel dès 326 j",
            min_value=1,
            value=325,
            step=1,
        )
        max_days_curve = st.number_input("Maturité max. grille (jours)", min_value=30, value=11000, step=50)
        step_short = st.number_input("Pas CT (jours)", min_value=1, value=50, step=1)
        step_long = st.number_input("Pas LT depuis 1300 j", min_value=1, value=100, step=1)

        st.divider()
        st.markdown("**Import Excel** (optionnel)")
        up = st.file_uploader("Fichier .xlsx", type=["xlsx"], help="Feuilles nommées CT/LT ou 1ère feuille 2 colonnes")
        if up is not None:
            parsed = parse_excel_curve(up)
            if parsed:
                st.session_state.short_df, st.session_state.long_df = parsed
                st.success("Données chargées.")
            else:
                st.warning("Lecture impossible — vérifiez le format.")

    c1, c2 = st.columns(2)
    with c1:
        st.markdown('<p class="wg-card-title">Piliers court terme (MM, %)</p>', unsafe_allow_html=True)
        short_ed = st.data_editor(
            st.session_state.short_df,
            num_rows="dynamic",
            use_container_width=True,
            key="short_editor",
        )
    with c2:
        st.markdown('<p class="wg-card-title">Piliers long terme (actuariel, %)</p>', unsafe_allow_html=True)
        long_ed = st.data_editor(
            st.session_state.long_df,
            num_rows="dynamic",
            use_container_width=True,
            key="long_editor",
        )

    try:
        curve = build_curve_from_edited(short_ed, long_ed, joint_days)
    except Exception as e:
        st.error(f"Données invalides : {e}")
        st.stop()

    grid = maturity_grid(max_days_curve, int(step_short), int(step_long), joint_days)
    ordered, _blocs = excel_style_maturity_order(grid, joint_days, int(step_short))
    table = curve.build_table(ordered)
    table_chart = curve.build_table(np.sort(np.unique(grid)))

    fig = plot_curve(table_chart)
    st.plotly_chart(fig, use_container_width=True)

    st.markdown("**Tableau courbe**")
    st.caption(
        "Ordre type Excel : bloc CT (1, 50, 100…), puis piliers (fond saumon), puis LT (≥1300 j)."
    )
    disp = table.copy()
    for col in ["Rate", "ZC_rate_continuous", "ZC_rate_annual_effective", "Actuarial_rate"]:
        disp[col] = disp[col] * 100.0
    disp = disp.rename(
        columns={
            "Maturity_days": "Maturité (jours)",
            "Year_fraction": "Temps (années)",
            "Rate": "Taux de marché (%)",
            "ZC_rate_annual_effective": "Taux ZC annuel (%)",
            "ZC_rate_continuous": "Taux ZC continu (%)",
            "Discount_factor": "Facteur d'actualisation",
            "Actuarial_rate": "Taux actuariel (%)",
        }
    )
    _col_order = [
        "Maturité (jours)",
        "Temps (années)",
        "Taux de marché (%)",
        "Taux ZC annuel (%)",
        "Taux ZC continu (%)",
        "Facteur d'actualisation",
        "Taux actuariel (%)",
    ]
    disp = disp[[c for c in _col_order if c in disp.columns]]
    for _c in ("Taux de marché (%)", "Taux ZC annuel (%)", "Taux ZC continu (%)", "Taux actuariel (%)"):
        if _c in disp.columns:
            disp[_c] = disp[_c].astype(float).round(3)
    if "Temps (années)" in disp.columns:
        disp["Temps (années)"] = disp["Temps (années)"].astype(float).round(4)
    if "Facteur d'actualisation" in disp.columns:
        disp["Facteur d'actualisation"] = disp["Facteur d'actualisation"].astype(float).round(6)
    _bench = set(FIXED_BENCHMARK_DAYS)

    def _style_benchmark(row: pd.Series) -> list[str]:
        d = int(row["Maturité (jours)"])
        if d in _bench:
            return ["background-color: #fce4d6"] * len(row)
        return [""] * len(row)

    st.dataframe(
        disp.style.apply(_style_benchmark, axis=1),
        use_container_width=True,
        height=320,
        hide_index=True,
    )

    st.divider()
    st.markdown(
        f'<div class="wg-card"><span class="wg-card-title">Pricing obligataire</span></div>',
        unsafe_allow_html=True,
    )
    b1, b2, b3, b4 = st.columns(4)
    with b1:
        nominal = st.number_input("Nominal", min_value=0.0, value=1_000_000.0, step=50_000.0)
    with b2:
        coupon_pct = st.number_input("Coupon annuel (%)", min_value=0.0, value=3.5, step=0.25) / 100.0
    with b3:
        mat_years = st.number_input("Maturité (années)", min_value=0.25, value=5.0, step=0.5)
    with b4:
        freq = st.selectbox("Fréquence / an", options=[1, 2, 4, 12], index=1)

    maturity_days = mat_years * 365.0
    try:
        cf_df, summ = bond_valuation_report(curve, nominal, coupon_pct, maturity_days, freq)
    except Exception as e:
        st.warning(str(e))
        cf_df, summ = pd.DataFrame(), pd.DataFrame()

    if not summ.empty:
        mcols = st.columns(len(summ))
        labels = {
            "Dirty_price": "Prix (dirty)",
            "YTM_actuarial": "YTM",
            "Macaulay_duration_years": "Duration Macaulay",
            "Modified_duration": "Duration mod.",
            "Convexity": "Convexité",
            "DV01_parallel_1bp": "DV01 (1bp)",
        }
        for i, (_, row) in enumerate(summ.iterrows()):
            metric = row["Metric"]
            val = row["Value"]
            with mcols[i]:
                if metric == "Dirty_price":
                    st.metric(labels.get(metric, metric), f"{val:,.2f}")
                elif metric == "YTM_actuarial":
                    st.metric(labels.get(metric, metric), f"{val * 100:.4f}%")
                elif "duration" in metric.lower() or "Duration" in metric:
                    st.metric(labels.get(metric, metric), f"{val:.4f}")
                elif metric == "Convexity":
                    st.metric(labels.get(metric, metric), f"{val:.4f}")
                elif metric == "DV01_parallel_1bp":
                    st.metric(labels.get(metric, metric), f"{val:,.2f}")
                else:
                    st.metric(metric, f"{val}")

    if not cf_df.empty:
        with st.expander("Flux de trésorerie actualisés", expanded=False):
            show = cf_df.copy()
            show["Cash_flow"] = show["Cash_flow"].map(lambda x: f"{x:,.2f}")
            show["PV"] = show["PV"].map(lambda x: f"{x:,.2f}")
            show["Actuarial_rate"] = (show["Actuarial_rate"] * 100).map(lambda x: f"{x:.4f}%")
            show["Discount_factor"] = show["Discount_factor"].map(lambda x: f"{x:.6f}")
            st.dataframe(show, use_container_width=True, hide_index=True)


if __name__ == "__main__":
    main()
