import { useEffect, useMemo, useRef, useState } from "react";
import {
  CartesianGrid,
  Cell,
  Bar,
  BarChart,
  Legend,
  Line,
  LineChart,
  Pie,
  PieChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import {
  getPortfolioValuation,
  getPortfolioHistory,
  postCurve,
  postCurvePillarsFromHisto,
  postMarcheValorize,
  prixValoAfficheMarche,
  type AmortissementTable,
  type CurveRequest,
  type MarcheValorizeRow,
  type PillarLong,
  type PillarShort,
  type PortfolioMbiTranche,
  type PortfolioFrequency,
  type PortfolioHistoryResponse,
  type PortfolioValuationResponse,
  type PrixManarrRow,
  type ScheduleRow,
} from "./api";
import BamInterpolationComparisonTable from "./components/BamInterpolationComparisonTable";

/** Clés renvoyées par l’API (apostrophe ASCII U+0027, identiques au backend). */
const MARCHE_KEY_DATE_EMISSION = "Date d'émission";
const MARCHE_KEY_DATE_ECHEANCE = "Date d'échéance";

const PORTFOLIO_MBI_OPTIONS: { value: PortfolioMbiTranche; label: string }[] = [
  { value: "global", label: "MBI Global (toutes les obligations)" },
  { value: "monetaire", label: "MBI Monétaire (< 1 an)" },
  { value: "ct", label: "MBI CT (1 à 3 ans)" },
  { value: "mt", label: "MBI MT (3 à 5 ans)" },
  { value: "mlt", label: "MBI MLT (5 à 10 ans)" },
  { value: "lt", label: "MBI LT (> 10 ans)" },
];

const PORTFOLIO_FREQUENCY_OPTIONS: { value: PortfolioFrequency; label: string }[] = [
  { value: "daily", label: "Journalière" },
  { value: "weekly", label: "Hebdomadaire" },
  { value: "monthly", label: "Mensuelle" },
  { value: "quarterly", label: "Trimestrielle" },
  { value: "yearly", label: "Annuelle" },
];

/** Filtre « tous les profils » dans la section Prix Manar. */
const MANARR_PROFIL_TOUS = "__all__";

function extractManarrProfil(sourceEcart: string | null | undefined): string {
  const s = String(sourceEcart ?? "").trim();
  if (!s) return "(sans profil)";
  const sep = " | ";
  if (s.includes(sep)) {
    const parts = s.split(sep);
    const last = parts[parts.length - 1]?.trim();
    if (last) return last;
  }
  const idx = s.lastIndexOf("|");
  if (idx >= 0) {
    const tail = s.slice(idx + 1).trim();
    if (tail) return tail;
  }
  return "(sans profil)";
}

function manarrLineStatus(sourceEcart: string | null | undefined): "acceptable" | "corriger" | "autre" {
  const lower = String(sourceEcart ?? "").toLowerCase();
  const ascii = lower.normalize("NFD").replace(/\p{M}/gu, "");
  if (lower.includes("acceptable")) return "acceptable";
  if (ascii.includes("a corriger")) return "corriger";
  return "autre";
}

function manarrEcartNum(row: PrixManarrRow): number | null {
  const x = row.ecart_prix_arrondi_valo;
  if (x == null || !Number.isFinite(Number(x))) return null;
  return Number(x);
}

function ManarrEcartSortGlyph({ mode }: { mode: "none" | "asc" | "desc" }) {
  const cActive = "var(--wg-primary)";
  const cMuted = "var(--wg-muted)";
  return (
    <svg width="11" height="12" viewBox="0 0 11 12" aria-hidden>
      <path
        d="M5.5 1.2 L8.8 4.8 H2.2 Z"
        fill={mode === "asc" ? cActive : cMuted}
        opacity={mode === "asc" ? 1 : 0.42}
      />
      <path
        d="M5.5 10.8 L8.8 7.2 H2.2 Z"
        fill={mode === "desc" ? cActive : cMuted}
        opacity={mode === "desc" ? 1 : 0.42}
      />
    </svg>
  );
}

const defaultShort: PillarShort[] = [];
const defaultLong: PillarLong[] = [];

/** Paramètres grille d’interpolation (fixes, plus affichés dans l’UI). */
const CURVE_GRID = {
  joint_days: 325,
  max_days: 11000,
  step_short: 50,
  step_long: 100,
} as const;

/**
 * Capital restant affiché type Excel AWB : ARRONDI(précédent − ARRONDI(amortissement;2);2),
 * aligné ``obligation_amort_schedule.construire_tableau_amortissement``.
 */
function postProcessCapitalRestantAWB(tab: AmortissementTable): AmortissementTable {
  /** Chaîne Excel sur le capital : uniquement pour les titres REV (hors REV = valeurs API inchangées). */
  if (!tab.pricing_rev_bond) {
    return tab;
  }
  const nom = tab.nominal_reference;
  if (nom === undefined || nom === null || !Number.isFinite(Number(nom))) {
    return tab;
  }
  const idxAm = tab.rows.findIndex((r) => r.label === "Amortissement");
  const idxCap = tab.rows.findIndex((r) => r.label === "Capital restant");
  if (idxAm < 0 || idxCap < 0) {
    return tab;
  }
  const amort = tab.rows[idxAm].values;
  const capPrev = tab.rows[idxCap].values;
  if (!amort.length || amort.length !== capPrev.length) {
    return tab;
  }
  const round2 = (x: number) => Math.round((x + 1e-12) * 100) / 100;
  let prev = round2(Number(nom));
  const newCap: number[] = [];
  for (let i = 0; i < amort.length; i++) {
    const raw = amort[i];
    const a = raw === null || raw === undefined || Number.isNaN(Number(raw)) ? 0 : Number(raw);
    if (a > 1e-6) {
      const step = Math.max(0, round2(prev - round2(a)));
      newCap.push(step);
      prev = step;
    } else {
      newCap.push(prev);
    }
  }
  const rows = tab.rows.map((r, ri) =>
    ri === idxCap ? { ...r, values: newCap } : r,
  );
  return { ...tab, rows };
}

/** AAAA-MM-JJ en fuseau **local** (évite le décalage d’un jour avec ``toISOString()`` en UTC). */
function localDateISO(): string {
  const d = new Date();
  const y = d.getFullYear();
  const m = String(d.getMonth() + 1).padStart(2, "0");
  const day = String(d.getDate()).padStart(2, "0");
  return `${y}-${m}-${day}`;
}

function formatIsoDateFr(iso: string): string {
  const parts = iso.split("-");
  if (parts.length < 3) return iso;
  const [y, m, d] = parts;
  return `${d}/${m}/${y}`;
}

function ecartPrixMrAffichage(prixArrondi: number, prixMr: number): number {
  const trunc2 = (x: number) => (x >= 0 ? Math.floor(x * 100) : Math.ceil(x * 100)) / 100;
  const pa2 = trunc2(Number(prixArrondi));
  const pmr2 = trunc2(Number(prixMr));
  const brut = pa2 - pmr2;
  if (!Number.isFinite(brut)) return 0;
  return Math.abs(brut) < 0.01 ? 0 : Math.round(brut * 100) / 100;
}

function formatAmortCell(v: number | null | undefined, fmt?: string): string {
  if (v === null || v === undefined || Number.isNaN(Number(v))) return "—";
  const n = Number(v);
  if (fmt === "pct") {
    return `${n.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 })}%`;
  }
  if (fmt === "pct5") {
    return `${n.toLocaleString("fr-FR", { minimumFractionDigits: 5, maximumFractionDigits: 5 })}%`;
  }
  if (fmt === "amount2") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  }
  /** Flux actualisé (ZC) : ``ARRONDI(H478/(1+H482)^H479;4)`` (voir backend). */
  if (fmt === "amount4") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 4, maximumFractionDigits: 4 });
  }
  /** Flux actualisé (REV) : ``round(prix, 5)`` comme Excel ARRONDI(...;5). */
  if (fmt === "amount5") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 5, maximumFractionDigits: 5 });
  }
  /** Durée Excel **H479** (feuille Ammortissable) : ``ARRONDI(…;10)``. */
  if (fmt === "dec10") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 10, maximumFractionDigits: 10 });
  }
  /** Durée (legacy 12 déc., ancien jours/365). */
  if (fmt === "dec12") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 12, maximumFractionDigits: 12 });
  }
  if (fmt === "dec3") {
    return n.toLocaleString("fr-FR", { minimumFractionDigits: 3, maximumFractionDigits: 3 });
  }
  return n.toLocaleString("fr-FR", { minimumFractionDigits: 2, maximumFractionDigits: 2 });
}

/** Référence des formules (aligné backend `_schedule_table_records`) ; B et z en décimal dans les formules. */
const SCHEDULE_ZC_COLUMN_RELATIONS: { column: string; role: string; relation: string }[] = [
  {
    column: "Maturité",
    role: "M — maturité en jours",
    relation:
      "Grille en jours : piliers CT SQL MAR_JJ (maturités < 365 j) + 365 j + (date + N cal. − date) pour N = 2…30 ; alignée backend `_schedule_table_records`.",
  },
  {
    column: "Taux",
    role: "B — taux marché (par / quotation)",
    relation:
      "Formule A : si M ≤ 326 → interp. CT (_mat1) ; M = 365 → interp. CT ; 326 < M < 365 → ((1+RM)^(M/365)−1)×360/M avec RM = interp. MLT ; M > 365 → interp. MLT (échéancier avec extrapolation au-delà du dernier pilier retenu).",
  },
  {
    column: "Année",
    role: "T — horizon en années",
    relation:
      "Si M < 365 : T = M/365 (fraction d’année). Si M ≥ 365 : T = ARRONDI(M/365 ; 0) (entier 1, 2, …). Utilisée comme exposant pour PXZC et pour le bootstrap.",
  },
  {
    column: "TauxZC",
    role: "z — taux zéro-coupon annuel (affichage 4 déc. %)",
    relation:
      "M < 365 : z = B. M = 365 : z = B×365/360 (= (1+365×B/360)−1). M > 365 : z = ((1+B)/(1−B×S))^(1/T) − 1 avec S = somme des PXZC (décimaux, pas %) des lignes précédentes où M ≥ 365.",
  },
  {
    column: "PXZC",
    role: "Facteur d’actualisation (×100 en % à l’écran)",
    relation:
      "Si M < 365 : vide. Si M ≥ 365 : PXZC = 1/(1+z)^T avec le z de la même ligne et T = Année.",
  },
  {
    column: "TauxZCActuariel",
    role: "Conversion / zéro-coupon actuariel (8 déc. %)",
    relation:
      "M < 365 : (1+B×M/360)^(365/M) − 1 (MM ACT/360 → actuariel annualisé base 365). M ≥ 365 : égal au z calculé pour TauxZC (cohérent avec PXZC).",
  },
];

/** Logo officiel horizontal (icône + texte) : `public/branding/wafa-gestion-logo.png` ; repli SVG si absent. */
function WafaGestionLogo() {
  const [src, setSrc] = useState("/branding/wafa-gestion-logo.png");
  return (
    <img
      className="wg-brand-logo"
      src={src}
      alt="Wafa Gestion"
      onError={() => setSrc("/branding/wafa-gestion-logo.svg")}
    />
  );
}

function buildCurvePayload(
  short: PillarShort[],
  long: PillarLong[],
  joint: number,
  maxDays: number,
  stepShort: number,
  stepLong: number,
  zcScheduleAnchorDate: string
): CurveRequest {
  return {
    short,
    long,
    joint_days: joint,
    max_days: maxDays,
    step_short: stepShort,
    step_long: stepLong,
    zc_schedule_anchor_date: zcScheduleAnchorDate,
  };
}

function formatBamRate(v: number): string {
  if (!Number.isFinite(Number(v))) return "—";
  return String(Number(v));
}

function formatAmount(v: number | null | undefined, digits = 2): string {
  if (!Number.isFinite(Number(v))) return "—";
  return Number(v).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  });
}

function formatPctFromDecimal(v: number | null | undefined, digits = 3): string {
  if (!Number.isFinite(Number(v))) return "—";
  return `${(Number(v) * 100).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

function formatPctNumber(v: number, digits = 3): string {
  if (!Number.isFinite(Number(v))) return "—";
  return `${Number(v).toLocaleString("fr-FR", {
    minimumFractionDigits: digits,
    maximumFractionDigits: digits,
  })}%`;
}

/** Construit les points du graphique ZC (valeurs finies uniquement ; secours si zc_pct manquant). */
function buildCurveChartRows(chart: {
  maturity_days: number[];
  zc_pct?: number[];
  actuarial_pct?: number[];
}): { maturity_days: number; zc_pct: number }[] {
  const md = chart.maturity_days ?? [];
  let zc = chart.zc_pct;
  if (!Array.isArray(zc) || zc.length !== md.length) {
    zc = chart.actuarial_pct;
  }
  if (!Array.isArray(zc) || zc.length !== md.length) {
    return [];
  }
  return md.map((d, i) => {
    const z = typeof zc![i] === "number" ? zc![i] : Number(zc![i]);
    return {
      maturity_days: Number(d),
      zc_pct: Number.isFinite(z) ? z : 0,
    };
  });
}

export default function App() {
  const [activeWorkspace, setActiveWorkspace] = useState<"valuation" | "portfolio" | "risk">("valuation");
  const [short, setShort] = useState<PillarShort[]>(defaultShort);
  const [long, setLong] = useState<PillarLong[]>(defaultLong);
  const [curveCalculated, setCurveCalculated] = useState(false);

  const [chartRows, setChartRows] = useState<{ maturity_days: number; zc_pct: number }[]>([]);
  const [scheduleTable, setScheduleTable] = useState<ScheduleRow[]>([]);
  const [curveLoading, setCurveLoading] = useState(false);
  const [marcheLoading, setMarcheLoading] = useState(false);
  const [histoLoading, setHistoLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [marcheRows, setMarcheRows] = useState<MarcheValorizeRow[]>([]);
  const [prixManarrRows, setPrixManarrRows] = useState<PrixManarrRow[]>([]);
  const [manarrProfilFilter, setManarrProfilFilter] = useState<string>(MANARR_PROFIL_TOUS);
  const [manarrEcartSort, setManarrEcartSort] = useState<"none" | "asc" | "desc">("none");
  const [valoriserPrixManarrTous, setValoriserPrixManarrTous] = useState(false);
  const [marcheDate, setMarcheDate] = useState<string>(localDateISO);
  const [marcheCode, setMarcheCode] = useState<string>("");
  const [amortissementTables, setAmortissementTables] = useState<AmortissementTable[]>([]);
  const [portfolioDate, setPortfolioDate] = useState<string>(localDateISO);
  const [portfolioStartDate, setPortfolioStartDate] = useState<string>("");
  const [portfolioEndDate, setPortfolioEndDate] = useState<string>("");
  const [portfolioFrequency, setPortfolioFrequency] = useState<PortfolioFrequency>("monthly");
  const [portfolioMbiTranche, setPortfolioMbiTranche] = useState<PortfolioMbiTranche>("global");
  const [portfolioLoading, setPortfolioLoading] = useState(false);
  const [portfolioError, setPortfolioError] = useState<string | null>(null);
  const [portfolioData, setPortfolioData] = useState<PortfolioValuationResponse | null>(null);
  const [portfolioHistory, setPortfolioHistory] = useState<PortfolioHistoryResponse | null>(null);
  /** Ignore les réponses d’une valorisation précédente si l’utilisateur relance avant la fin. */
  const marcheValorizeReqSeq = useRef(0);

  const manarrProfilOptions = useMemo(() => {
    const uniq = new Set<string>();
    for (const r of prixManarrRows) {
      uniq.add(extractManarrProfil(r.source_ecart));
    }
    return Array.from(uniq).sort((a, b) => a.localeCompare(b, "fr"));
  }, [prixManarrRows]);

  const manarrProfilCounts = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of prixManarrRows) {
      const p = extractManarrProfil(r.source_ecart);
      m.set(p, (m.get(p) ?? 0) + 1);
    }
    return m;
  }, [prixManarrRows]);

  /** Profils dont chaque ligne est « acceptable » (repère visuel dans le sélecteur). */
  const manarrProfilToutAcceptable = useMemo(() => {
    const keys = new Set<string>();
    for (const r of prixManarrRows) {
      keys.add(extractManarrProfil(r.source_ecart));
    }
    const m = new Map<string, boolean>();
    for (const p of keys) {
      const rows = prixManarrRows.filter((r) => extractManarrProfil(r.source_ecart) === p);
      m.set(
        p,
        rows.length > 0 && rows.every((r) => manarrLineStatus(r.source_ecart) === "acceptable"),
      );
    }
    return m;
  }, [prixManarrRows]);

  const manarrTousProfilsFichierAcceptable = useMemo(
    () =>
      prixManarrRows.length > 0 &&
      prixManarrRows.every((r) => manarrLineStatus(r.source_ecart) === "acceptable"),
    [prixManarrRows],
  );

  const manarrFilteredRows = useMemo(() => {
    if (manarrProfilFilter === MANARR_PROFIL_TOUS) return prixManarrRows;
    return prixManarrRows.filter(
      (r) => extractManarrProfil(r.source_ecart) === manarrProfilFilter
    );
  }, [prixManarrRows, manarrProfilFilter]);

  const manarrDisplayRows = useMemo(() => {
    if (manarrEcartSort === "none") return manarrFilteredRows;
    const rows = [...manarrFilteredRows];
    rows.sort((a, b) => {
      const va = manarrEcartNum(a);
      const vb = manarrEcartNum(b);
      const aMiss = va === null;
      const bMiss = vb === null;
      if (aMiss && bMiss) return 0;
      if (aMiss) return 1;
      if (bMiss) return -1;
      const cmp = va - vb;
      return manarrEcartSort === "asc" ? cmp : -cmp;
    });
    return rows;
  }, [manarrFilteredRows, manarrEcartSort]);

  const manarrStats = useMemo(() => {
    const total = manarrFilteredRows.length;
    let ok = 0;
    let corr = 0;
    let other = 0;
    for (const r of manarrFilteredRows) {
      const st = manarrLineStatus(r.source_ecart);
      if (st === "acceptable") ok += 1;
      else if (st === "corriger") corr += 1;
      else other += 1;
    }
    const pct = (n: number) => (total > 0 ? Math.round((1000 * n) / total) / 10 : 0);
    return {
      total,
      ok,
      corr,
      other,
      pctOk: pct(ok),
      pctCorr: pct(corr),
      pctOther: pct(other),
    };
  }, [manarrFilteredRows]);

  const manarrChartData = useMemo(
    () =>
      [
        { name: "Acceptable", value: manarrStats.ok, fill: "#15803d" },
        { name: "À corriger", value: manarrStats.corr, fill: "#b91c1c" },
        { name: "Autre / inconnu", value: manarrStats.other, fill: "#78716c" },
      ].filter((d) => d.value > 0),
    [manarrStats.corr, manarrStats.ok, manarrStats.other],
  );

  useEffect(() => {
    if (manarrProfilFilter === MANARR_PROFIL_TOUS) return;
    if (!manarrProfilOptions.includes(manarrProfilFilter)) {
      setManarrProfilFilter(MANARR_PROFIL_TOUS);
    }
  }, [manarrProfilFilter, manarrProfilOptions]);

  const portfolioPositionsSorted = useMemo(() => {
    if (!portfolioData) return [];
    return [...portfolioData.positions].sort(
      (a, b) => Number(b.weight ?? 0) - Number(a.weight ?? 0),
    );
  }, [portfolioData]);

  const portfolioRiskContributions = useMemo(() => {
    if (!portfolioData) return [];
    const totalDv01 = Number(portfolioData.summary.portfolio_dv01 ?? 0);
    return [...portfolioData.positions]
      .map((p) => {
        const dv01 = Number(p.dv01 ?? 0);
        return {
          ...p,
          dv01,
          contribution_dv01_pct: totalDv01 > 0 ? dv01 / totalDv01 : 0,
        };
      })
      .sort((a, b) => b.dv01 - a.dv01)
      .slice(0, 10);
  }, [portfolioData]);
  const portfolioMode = portfolioStartDate && portfolioEndDate ? "history" : "snapshot";
  const portfolioAllocationRows = useMemo(() => {
    const a = portfolioData?.allocations;
    return {
      sectors: (a?.by_sector ?? []).slice(0, 8),
      maturity: a?.by_maturity ?? [],
      issuers: (a?.by_issuer ?? []).slice(0, 8),
      risk: (a?.risk_contribution ?? portfolioRiskContributions).slice(0, 10),
    };
  }, [portfolioData, portfolioRiskContributions]);
  const historyChartRows = useMemo(
    () =>
      (portfolioHistory?.series ?? []).map((p) => ({
        ...p,
        dateLabel: formatIsoDateFr(p.date),
        nav_m: Number(p.raw_nav ?? p.nav ?? 0) / 1_000_000,
        index_base_100: Number(p.index_base_100 ?? 100),
        cumulative_pct: Number(p.cumulative_return ?? 0) * 100,
        drawdown_pct: Number(p.drawdown ?? 0) * 100,
        ytm_pct: Number(p.ytm ?? 0) * 100,
        spread_pct: Number(p.spread ?? 0),
        sensibilite: Number(p.sensibilite ?? 0),
        entries_count: Number(p.entries_count ?? 0),
        exits_count: Number(p.exits_count ?? 0),
        turnover_pct: Number(p.universe_turnover ?? 0) * 100,
      })),
    [portfolioHistory],
  );
  const returnHistogramRows = useMemo(() => {
    const returns = portfolioHistory?.returns ?? [];
    if (returns.length === 0) return [];
    const min = Math.min(...returns);
    const max = Math.max(...returns);
    const bins = 12;
    const width = max > min ? (max - min) / bins : 0.0001;
    return Array.from({ length: bins }, (_, i) => {
      const lo = min + i * width;
      const hi = lo + width;
      const count = returns.filter((r, idx) => r >= lo && (idx === returns.length - 1 ? r <= hi : r < hi)).length;
      return { bucket: `${(lo * 100).toFixed(2)}%`, count };
    });
  }, [portfolioHistory]);
  const headerDateLong = useMemo(
    () =>
      new Date().toLocaleDateString("fr-FR", {
        weekday: "long",
        day: "numeric",
        month: "long",
        year: "numeric",
      }),
    []
  );

  const curvePayload = useMemo(
    () =>
      buildCurvePayload(
        short,
        long,
        CURVE_GRID.joint_days,
        CURVE_GRID.max_days,
        CURVE_GRID.step_short,
        CURVE_GRID.step_long,
        marcheDate
      ),
    [short, long, marcheDate]
  );

  async function tracerLaCourbe() {
    setError(null);
    setCurveLoading(true);
    try {
      const res = await postCurve(curvePayload);
      setScheduleTable(res.schedule_table ?? []);
      setChartRows(buildCurveChartRows(res.chart));
      setCurveCalculated(true);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Erreur");
      setChartRows([]);
      setScheduleTable([]);
      setCurveCalculated(false);
    } finally {
      setCurveLoading(false);
    }
  }

  async function chargerPiliersDepuisHisto(): Promise<CurveRequest> {
    setShort([]);
    setLong([]);
    const res = await postCurvePillarsFromHisto({
      date_courbe: marcheDate,
      courbe: "MAR_JJ",
    });
    setShort(res.short);
    setLong(res.long);
    if (res.date_used !== marcheDate) {
      setShort([]);
      setLong([]);
      throw new Error(
        `Aucun taux BAM n'est disponible pour la date ${formatIsoDateFr(marcheDate)}. Corrige la date puis relance la valorisation.`,
      );
    }
    const jointFromHisto =
      typeof res.joint_days === "number" && Number.isFinite(res.joint_days)
        ? res.joint_days
        : CURVE_GRID.joint_days;
    return buildCurvePayload(
      res.short,
      res.long,
      jointFromHisto,
      CURVE_GRID.max_days,
      CURVE_GRID.step_short,
      CURVE_GRID.step_long,
      marcheDate
    );
  }

  async function runMarcheValorization(prixManarrTous = valoriserPrixManarrTous) {
    const reqId = ++marcheValorizeReqSeq.current;
    setError(null);
    setMarcheRows([]);
    setPrixManarrRows([]);
    setManarrProfilFilter(MANARR_PROFIL_TOUS);
    setManarrEcartSort("none");
    setAmortissementTables([]);
    setMarcheLoading(true);
    setHistoLoading(true);
    try {
      const freshCurvePayload = await chargerPiliersDepuisHisto();
      if (reqId !== marcheValorizeReqSeq.current) return;
      const { data: res, amortEngineIdHeader } = await postMarcheValorize({
        valuation_date: marcheDate,
        curve: freshCurvePayload,
        // Toujours envoyer les piliers CT/LT courants : la ligne « Taux AA » du tableau d’amortissement
        // (METHODE_VALO AA) suit la même Formule B que « Comparaison interpolation BAM » ; METHODE_VALO ZC
        // utilise l’échéancier annuel dérivé de cette même courbe (TauxZCActuariel).
        prix_manarr_pricer_tous: prixManarrTous,
        ...(marcheCode.trim() ? { code_maroclear: marcheCode.trim() } : {}),
      });
      if (reqId !== marcheValorizeReqSeq.current) return;
      if (res.nb_lignes > 0 && !amortEngineIdHeader) {
        setError(
          "Réponse sans en-tête X-Pricer-Amort-Engine-ID : le proxy / le port 8001 ne sert pas ce Pricer. Fermez les autres Python, lancez run-pricer.bat ou python run_api.py depuis le dossier du projet.",
        );
      }
      const engJson = res.diagnostic?.amort_engine_id;
      if (
        amortEngineIdHeader &&
        engJson != null &&
        String(engJson).length > 0 &&
        amortEngineIdHeader !== String(engJson)
      ) {
        setError(
          `Incohérence moteur : en-tête HTTP « ${amortEngineIdHeader} » ≠ diagnostic « ${engJson} ». Redémarrez l’API.`,
        );
      }
      const f1 = Array.isArray(res.feuil1_titres) ? res.feuil1_titres : [];
      const normCode = (v: unknown): string => {
        const s = String(v ?? "").trim();
        if (/^[0-9]+\.0+$/.test(s)) return s.split(".", 1)[0];
        return s;
      };
      const mapTitreValo = new Map<string, number>();
      for (const t of f1) {
        const code = normCode(t.titre);
        if (!code || t.valo == null || !Number.isFinite(Number(t.valo))) continue;
        mapTitreValo.set(code, Number(t.valo));
      }
      // « Prix arrondi » = lecture cohérente de la réponse API (voir ``prixValoAfficheMarche``).
      const rowsPrixMr = (res.rows ?? []).map((r) => {
        const code = normCode(r.CODE);
        const pmr = code ? mapTitreValo.get(code) : undefined;
        if (pmr == null) {
          return { ...r, "Prix MR": null, "Ecart Prix arrondi - Prix MR": null };
        }
        const pa = prixValoAfficheMarche(r);
        const pmr2 = Math.round(pmr * 100) / 100;
        const ecart = ecartPrixMrAffichage(pa, pmr2);
        return { ...r, "Prix MR": pmr2, "Ecart Prix arrondi - Prix MR": ecart };
      });
      setMarcheRows(rowsPrixMr);
      setPrixManarrRows(Array.isArray(res.prix_manarr) ? res.prix_manarr : []);
      setManarrProfilFilter(MANARR_PROFIL_TOUS);
      setAmortissementTables(res.amortissement_tables ?? []);
    } catch (e) {
      setMarcheRows([]);
      setPrixManarrRows([]);
      setAmortissementTables([]);
      const msg = e instanceof Error ? e.message : "Erreur valorisation";
      setError(msg);
    } finally {
      setMarcheLoading(false);
      setHistoLoading(false);
    }
  }

  async function runPortfolioValuation() {
    setPortfolioError(null);
    setPortfolioLoading(true);
    try {
      if (portfolioStartDate && portfolioEndDate) {
        const res = await getPortfolioHistory({
          date_debut: portfolioStartDate,
          date_fin: portfolioEndDate,
          mbi_tranche: portfolioMbiTranche,
          frequence: portfolioFrequency,
        });
        setPortfolioHistory(res);
        setPortfolioData(null);
      } else {
        const res = await getPortfolioValuation({
          date_valo: portfolioEndDate || portfolioDate,
          mbi_tranche: portfolioMbiTranche,
        });
        setPortfolioData(res);
        setPortfolioHistory(null);
      }
    } catch (e) {
      setPortfolioData(null);
      setPortfolioHistory(null);
      setPortfolioError(e instanceof Error ? e.message : "Erreur portefeuille");
    } finally {
      setPortfolioLoading(false);
    }
  }

  return (
    <div className="wg-app-shell">
      <header className="wg-top-header" role="banner">
        <div className="wg-top-header-inner">
          <div className="wg-brand-block">
            <WafaGestionLogo />
          </div>
          <div className="wg-title-block">
            <h1>Pricer Wafa Gestion</h1>
            <p className="wg-title-date">{headerDateLong}</p>
          </div>
        </div>
      </header>

      <div className="wg-workspace">
        <aside className="wg-sidebar" aria-label="Navigation métier">
          <nav className="wg-sidebar-nav">
            <button
              type="button"
              className={`wg-sidebar-item ${activeWorkspace === "valuation" ? "is-active" : ""}`}
              onClick={() => setActiveWorkspace("valuation")}
            >
              Valorisation des obligations actives
            </button>
            <button
              type="button"
              className={`wg-sidebar-item ${activeWorkspace === "portfolio" ? "is-active" : ""}`}
              onClick={() => setActiveWorkspace("portfolio")}
            >
              Gestion de portefeuille
            </button>
            <button
              type="button"
              className={`wg-sidebar-item ${activeWorkspace === "risk" ? "is-active" : ""}`}
              onClick={() => setActiveWorkspace("risk")}
            >
              Gestion de risque
            </button>
          </nav>
          <div className="wg-sidebar-footer">
            <strong>Wafa Gestion</strong>
            Pricer obligataire
          </div>
        </aside>

        <main className="wg-workspace-main">
          {activeWorkspace === "valuation" ? (
            <>
      <section
        style={{
          background: "var(--wg-card)",
          border: "1px solid var(--wg-border)",
          borderRadius: 10,
          padding: "0.85rem 1rem",
          marginBottom: "0.75rem",
        }}
      >
        <h2
          style={{
            color: "var(--wg-primary)",
            fontSize: "0.95rem",
            margin: "0 0 0.75rem",
            borderBottom: "2px solid var(--wg-accent)",
            display: "inline-block",
            paddingBottom: 4,
          }}
        >
          Lancement valorisation active
        </h2>
        <div className="bond-form-grid" style={{ marginTop: 0 }}>
          <label>
            <span>Date de valorisation</span>
            <input type="date" value={marcheDate} onChange={(e) => setMarcheDate(e.target.value)} />
          </label>
          <label>
            <span>Code Maroclear (optionnel)</span>
            <input
              type="text"
              value={marcheCode}
              onChange={(e) => setMarcheCode(e.target.value)}
              placeholder="Ex: 9530"
            />
          </label>
        </div>
        <div style={{ display: "flex", flexWrap: "wrap", gap: "0.5rem" }}>
          <button type="button" onClick={() => runMarcheValorization()} disabled={marcheLoading || histoLoading} className="phase-action-btn">
            {marcheLoading || histoLoading ? "Chargement SQL et calcul..." : "Valoriser l'obligation active"}
          </button>
          <label style={{ display: "inline-flex", alignItems: "center", gap: "0.45rem", color: "var(--wg-text)", fontWeight: 600 }}>
            <input
              type="checkbox"
              checked={valoriserPrixManarrTous}
              disabled={marcheLoading || histoLoading}
              onChange={(e) => {
                const checked = e.target.checked;
                setValoriserPrixManarrTous(checked);
                if (checked) {
                  void runMarcheValorization(true);
                }
              }}
            />
            <span>valoriser tout ces obligation</span>
          </label>
        </div>
        {error && (
          <div style={{ background: "#fef2f2", color: "#b91c1c", padding: "0.75rem", borderRadius: 8, marginTop: "0.75rem" }}>
            {error}
          </div>
        )}
      </section>

      <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: "0.75rem", marginBottom: "0.75rem" }}>
        <section
          style={{
            background: "var(--wg-card)",
            border: "1px solid var(--wg-border)",
            borderRadius: 10,
            padding: "0.85rem 1rem",
          }}
        >
          <h2
            style={{
              color: "var(--wg-primary)",
              fontSize: "0.95rem",
              margin: "0 0 0.75rem",
              borderBottom: "2px solid var(--wg-accent)",
              display: "inline-block",
              paddingBottom: 4,
            }}
          >
            Piliers court terme (MM %)
          </h2>
          <p style={{ margin: "0 0 0.6rem", fontSize: "0.78rem", color: "var(--wg-muted)", maxWidth: 480 }}>
            Points BAM réels en court terme, chargés depuis <code>dbo.histo_courbe_taux</code>.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Maturité (j)</th>
                <th>Taux MM (%)</th>
              </tr>
            </thead>
            <tbody>
              {short.map((row, i) => (
                <tr key={i}>
                  <td>{row.maturity_days}</td>
                  <td className="mono">{formatBamRate(row.mm_rate_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>

        <section
          style={{
            background: "var(--wg-card)",
            border: "1px solid var(--wg-border)",
            borderRadius: 10,
            padding: "0.85rem 1rem",
          }}
        >
          <h2
            style={{
              color: "var(--wg-primary)",
              fontSize: "0.95rem",
              margin: "0 0 0.75rem",
              borderBottom: "2px solid var(--wg-accent)",
              display: "inline-block",
              paddingBottom: 4,
            }}
          >
            Piliers long terme (actuariel %)
          </h2>
          <p style={{ margin: "0 0 0.6rem", fontSize: "0.78rem", color: "var(--wg-muted)", maxWidth: 420 }}>
            Points BAM réels en long terme, chargés depuis <code>dbo.histo_courbe_taux</code>.
          </p>
          <table className="data-table">
            <thead>
              <tr>
                <th>Maturité (j)</th>
                <th>Taux (%)</th>
              </tr>
            </thead>
            <tbody>
              {long.map((row, i) => (
                <tr key={i}>
                  <td>{row.maturity_days}</td>
                  <td className="mono">{formatBamRate(row.actuarial_rate_pct)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      </div>

      <div
        style={{
          display: "flex",
          justifyContent: "center",
          marginBottom: "1rem",
        }}
      >
        <button
          type="button"
          onClick={tracerLaCourbe}
          disabled={curveLoading}
          style={{
            background: "var(--wg-accent)",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "0.65rem 1.75rem",
            fontWeight: 600,
            fontSize: "1rem",
            cursor: curveLoading ? "wait" : "pointer",
            opacity: curveLoading ? 0.85 : 1,
            boxShadow: "0 2px 8px rgba(13, 148, 136, 0.35)",
          }}
        >
          {curveLoading ? "Calcul en cours…" : "Tracer la courbe"}
        </button>
      </div>

      {(curveLoading || curveCalculated) && <BamInterpolationComparisonTable shortPillars={short} longPillars={long} />}

      {curveCalculated && (
        <>
          <section
            style={{
              background: "var(--wg-card)",
              border: "1px solid var(--wg-border)",
              borderRadius: 10,
              padding: "1rem",
              marginBottom: "1rem",
              overflow: "auto",
            }}
          >
            <h2 style={{ margin: "0 0 0.75rem", fontSize: "1.1rem", color: "var(--wg-text)" }}>
              Échéancier annuel (ZC)
            </h2>
            <p style={{ margin: "0 0 0.75rem", fontSize: "0.8rem", color: "var(--wg-muted)" }}>
              Maturités fixes et colonnes type Excel : Taux marché, ZC annuel, prix ZC (nominal 100), taux actuariel.
            </p>
            {curveLoading && (
              <p style={{ color: "var(--wg-muted)", textAlign: "center", margin: "1rem 0" }}>Calcul…</p>
            )}
            {!curveLoading && scheduleTable.length === 0 && (
              <p style={{ color: "var(--wg-muted)", textAlign: "center", margin: "1rem 0" }}>
                Disponible après <strong>Tracer la courbe</strong>.
              </p>
            )}
            {!curveLoading && scheduleTable.length > 0 && (
              <table className="data-table schedule-table">
                <thead>
                  <tr>
                    <th className="schedule-th-market">Maturité</th>
                    <th className="schedule-th-market">Taux</th>
                    <th className="schedule-th-market">Année</th>
                    <th className="schedule-th-zc">TauxZC</th>
                    <th className="schedule-th-zc">PXZC</th>
                    <th className="schedule-th-zc">TauxZCActuariel</th>
                  </tr>
                </thead>
                <tbody>
                  {scheduleTable.map((row, i) => (
                    <tr key={i}>
                      <td>{Math.round(row.Maturity_days)}</td>
                      <td>{row.Taux_pct.toFixed(8)}</td>
                      <td>{i < 4 ? row.Annee.toFixed(9) : row.Annee >= 1 ? Math.round(row.Annee) : row.Annee.toFixed(4)}</td>
                      <td>{row.Taux_ZC_pct.toFixed(4)}</td>
                      <td>{row.PXZC == null ? "" : `${(row.PXZC * 100).toFixed(4)}%`}</td>
                      <td>{row.Taux_ZC_actuariel_pct.toFixed(8)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            )}

            <details className="schedule-zc-relations-details">
              <summary className="schedule-zc-relations-summary">
                <span className="schedule-zc-relations-summary-label">Détail des calculs (colonnes)</span>
                <span className="schedule-zc-relations-summary-hint">Cliquer pour afficher ou masquer les formules</span>
              </summary>
              <div className="schedule-zc-relations-inner">
                <p className="schedule-zc-relations-intro">
                  Correspondance avec la feuille Excel : mêmes définitions que le calcul serveur pour l’échéancier ZC (bootstrap
                  sur taux par B, base 360 pour le court terme, exposant T en années).
                </p>
                <table className="data-table schedule-relations-table">
                  <thead>
                    <tr>
                      <th scope="col">Colonne</th>
                      <th scope="col">Signification</th>
                      <th scope="col">Relation / formule</th>
                    </tr>
                  </thead>
                  <tbody>
                    {SCHEDULE_ZC_COLUMN_RELATIONS.map((r) => (
                      <tr key={r.column}>
                        <td className="schedule-relations-col-name">{r.column}</td>
                        <td>{r.role}</td>
                        <td className="schedule-relations-formula">{r.relation}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </details>
          </section>

          <section
            style={{
              background: "var(--wg-card)",
              border: "1px solid var(--wg-border)",
              borderRadius: 10,
              padding: "1rem",
              marginBottom: "1rem",
            }}
          >
            <h2 style={{ margin: "0 0 0.35rem", fontSize: "1.1rem", color: "var(--wg-text)" }}>
              Courbe des taux zéro-coupon
            </h2>
            <p style={{ margin: "0 0 1rem", fontSize: "0.8rem", color: "var(--wg-muted)" }}>
              Mêmes points que la colonne <strong>TauxZC</strong> du tableau « Échéancier annuel (ZC) » (bootstrap à partir du
              Taux marché sur les maturités fixes).
            </p>
            {curveLoading && (
              <p style={{ color: "var(--wg-muted)", margin: "2rem 0", textAlign: "center" }}>Préparation du graphique…</p>
            )}
            {!curveLoading && chartRows.length > 0 && (
              <ResponsiveContainer width="100%" height={420}>
                <LineChart
                  data={chartRows}
                  margin={{ top: 8, right: 16, left: 8, bottom: 8 }}
                >
                  <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
                  <XAxis
                    type="number"
                    dataKey="maturity_days"
                    domain={["dataMin", "dataMax"]}
                    scale="linear"
                    name="Jours"
                    tick={{ fontSize: 11 }}
                    label={{ value: "Maturité (jours)", position: "insideBottom", offset: -4, fontSize: 11 }}
                  />
                  <YAxis
                    type="number"
                    domain={["auto", "auto"]}
                    width={56}
                    tick={{ fontSize: 11 }}
                    tickFormatter={(v) => `${Number(v).toFixed(2)}%`}
                    label={{ value: "Taux ZC (%)", angle: -90, position: "insideLeft", fontSize: 11 }}
                  />
                  <Tooltip
                    formatter={(v: number) => [`${Number(v).toFixed(4)}%`, "Taux ZC"]}
                    labelFormatter={(l) => `Maturité ${l} j`}
                  />
                  <Legend />
                  <Line
                    type="linear"
                    dataKey="zc_pct"
                    name="Taux ZC"
                    stroke="#0e4b99"
                    strokeWidth={2.5}
                    dot={false}
                    isAnimationActive={false}
                    connectNulls
                  />
                </LineChart>
              </ResponsiveContainer>
            )}
            {!curveLoading && chartRows.length === 0 && (
              <p style={{ color: "var(--wg-muted)", margin: "1.5rem 0", textAlign: "center" }}>
                Le graphique s&apos;affichera ici après <strong>Tracer la courbe</strong>.
              </p>
            )}
          </section>
        </>
      )}

      <section className="pricing-section">
        <div className="pricing-header">
          <h2>Pricing obligataire</h2>
        </div>

        {marcheRows.length > 0 && (
          <>
          <div className="pricing-stack-grid" style={{ marginTop: "0.5rem" }}>
            <article className="pricing-panel" style={{ overflow: "auto" }}>
              <h3 className="pricing-panel-title">Valorisation</h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>CODE</th>
                    <th>Taux facial utilisé (coupon couru)</th>
                    <th>Description</th>
                    <th>Prix arrondi</th>
                    <th>Coupon couru</th>
                  </tr>
                </thead>
                <tbody>
                  {marcheRows.slice(0, 200).map((r, i) => (
                    <tr key={`${r.CODE}-${i}`}>
                      <td>{String(r.CODE)}</td>
                      <td>
                        {`${Number(r["Taux facial utilisé (coupon couru)"] ?? r.TAUX ?? 0).toLocaleString(
                          "fr-FR",
                          {
                            minimumFractionDigits: 2,
                            maximumFractionDigits: 4,
                          }
                        )}%`}
                      </td>
                      <td>{r.description}</td>
                      <td>
                        {prixValoAfficheMarche(r).toLocaleString("fr-FR", {
                          minimumFractionDigits: 6,
                          maximumFractionDigits: 6,
                        })}
                      </td>
                      <td>{Number(r["Coupon couru"] ?? 0).toFixed(2)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>

            <article className="pricing-panel" style={{ overflow: "auto" }}>
              <h3 className="pricing-panel-title">Métriques de risque</h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>CODE</th>
                    <th>YTM</th>
                    <th>Duration</th>
                    <th>Sensibilité</th>
                    <th>Convexité</th>
                    <th>Mr (jours)</th>
                  </tr>
                </thead>
                <tbody>
                  {marcheRows.slice(0, 200).map((r, i) => (
                    <tr key={`risk-${r.CODE}-${i}`}>
                      <td>{String(r.CODE)}</td>
                      <td>{`${(Number(r["Rendement (YTM)"] ?? 0) * 100).toFixed(3)}%`}</td>
                      <td>{Number(r["Duration titre"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Sensibilité"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Convexité"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Maturité résiduelle (jours)"] ?? 0)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>
          </div>

            <article className="pricing-panel" style={{ marginTop: "0.75rem", overflow: "auto" }}>
              <h3 className="pricing-panel-title">Synthèse titre</h3>
              <table className="data-table">
                <thead>
                  <tr>
                    <th>CODE</th>
                    <th>Taux facial utilisé (coupon couru)</th>
                    <th>Description</th>
                    <th>Nominal</th>
                    <th>Prix</th>
                    <th>Rendement</th>
                    <th>Spread</th>
                    <th>Duration</th>
                    <th>Sensibilité</th>
                    <th>Convexité</th>
                    <th>Maturité résiduelle</th>
                    <th>Coupon couru</th>
                    <th>Date d&apos;émission</th>
                    <th>Date échéance</th>
                  </tr>
                </thead>
                <tbody>
                  {marcheRows.slice(0, 200).map((r, i) => (
                    <tr key={`synth-${r.CODE}-${i}`}>
                      <td>{String(r.CODE)}</td>
                      <td>{`${Number(r["Taux facial utilisé (coupon couru)"] ?? r.TAUX ?? 0).toLocaleString("fr-FR", {
                        minimumFractionDigits: 2,
                        maximumFractionDigits: 4,
                      })}%`}</td>
                      <td style={{ maxWidth: 280, whiteSpace: "normal" }}>
                        {r.Description ?? r.description}
                      </td>
                      <td>
                        {Number(
                          r.Nominal ??
                            (r as unknown as Record<string, number | undefined>).NOMINAL ??
                            0
                        ).toLocaleString("fr-FR", {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                      <td>
                        {Number(r["Prix dirty"] ?? r["Prix arrondi"] ?? 0).toLocaleString("fr-FR", {
                          minimumFractionDigits: 2,
                          maximumFractionDigits: 2,
                        })}
                      </td>
                      <td>{`${(Number(r["Rendement (YTM)"] ?? 0) * 100).toFixed(3)}%`}</td>
                      <td>{`${Number(r.Spread ?? 0).toFixed(3)}%`}</td>
                      <td>{Number(r["Duration titre"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Sensibilité"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Convexité"] ?? 0).toFixed(6)}</td>
                      <td>{Number(r["Maturité résiduelle (jours)"] ?? 0)}</td>
                      <td>{Number(r["Coupon couru"] ?? 0).toFixed(2)}</td>
                      <td>{String((r as Record<string, unknown>)[MARCHE_KEY_DATE_EMISSION] ?? "")}</td>
                      <td>{String((r as Record<string, unknown>)[MARCHE_KEY_DATE_ECHEANCE] ?? "")}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </article>

            {marcheRows.length > 0 && amortissementTables.length === 0 && (
              <article
                className="pricing-panel"
                style={{
                  marginTop: "0.75rem",
                  borderLeft: "4px solid #c2410c",
                  background: "rgba(194, 65, 12, 0.06)",
                }}
              >
                <h3 className="pricing-panel-title">Échéancier d&apos;amortissement</h3>
                <p className="phase-help" style={{ marginBottom: 0 }}>
                  Le tableau détaillé (amortissement, flux, actualisation) ne s&apos;affiche que si le même classeur
                  Excel contient une feuille d&apos;échéancier reconnue (nom contenant <strong>echeancier</strong>, ex.{" "}
                  <code>echeancier_Titre</code>) avec les colonnes <strong>CODE</strong> ou <strong>TITRE</strong>, une{" "}
                  <strong>date</strong> de tombée et un <strong>amortissement</strong>. Vérifiez aussi le message orange
                  sous le bouton de valorisation : il indique si la feuille est absente ou si une erreur a été bloquée.
                </p>
              </article>
            )}

            {amortissementTables.length > 0 && (
              <div className="pricing-amort-stack" style={{ marginTop: "0.75rem" }}>
                {amortissementTables.map((tab, ti) => {
                  const tabAmort = postProcessCapitalRestantAWB(tab);
                  return (
                  <article key={`amort-${tab.code}-${ti}`} className="pricing-panel" style={{ overflow: "auto" }}>
                    <h3 className="pricing-panel-title">
                      Échéancier d&apos;amortissement — CODE {String(tab.code)}
                    </h3>
                    <div style={{ overflowX: "auto" }}>
                      <table className="data-table" style={{ minWidth: 640, fontSize: "0.85rem" }}>
                        <thead>
                          <tr>
                            <th style={{ position: "sticky", left: 0, background: "var(--panel-bg, #fff)", zIndex: 1 }}>
                              &nbsp;
                            </th>
                            {tabAmort.columns.map((c) => (
                              <th key={c}>{formatIsoDateFr(c)}</th>
                            ))}
                          </tr>
                        </thead>
                        <tbody>
                          {tabAmort.rows.map((row) => (
                            <tr key={row.label}>
                              <th
                                scope="row"
                                style={{
                                  position: "sticky",
                                  left: 0,
                                  background: "var(--panel-bg, #f8f9fa)",
                                  textAlign: "left",
                                  whiteSpace: "nowrap",
                                }}
                              >
                                {row.label}
                              </th>
                              {tabAmort.columns.map((colKey, vi) => {
                                const v = row.values[vi];
                                return (
                                  <td key={`${row.label}-${colKey}-${vi}`}>
                                    {formatAmortCell(v ?? null, row.format)}
                                  </td>
                                );
                              })}
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </article>
                  );
                })}
              </div>
            )}

            {prixManarrRows.length > 0 && (
              <article className="pricing-panel" style={{ marginTop: "0.75rem", overflow: "auto" }}>
                <h3 className="pricing-panel-title">Prix Manar</h3>
                <p className="phase-help" style={{ marginTop: "0.35rem", marginBottom: "0.65rem" }}>
                  <strong>Valo (fichier)</strong> : montant lu dans votre classeur Prix Manar (colonne « valo »).{" "}
                  <strong>Prix arrondi (moteur)</strong> : prix clean du Pricer pour le même code que dans
                  Valorisation (rempli à chaque valorisation lorsque le titre est dans la réponse).{" "}
                  <strong>Écart</strong> = prix moteur − valo fichier (contrôle). Seuls les codes{" "}
                  <strong>numériques</strong> Maroclear sont affichés (devises et ISIN « XS… » exclus). La case «
                  valoriser tout » refait en plus un recalcul complet pour toutes les lignes du fichier.
                </p>
                <div className="bond-form-grid" style={{ maxWidth: 480 }}>
                  <label>
                    <span>Profil obligation</span>
                    <select
                      value={manarrProfilFilter}
                      onChange={(e) => setManarrProfilFilter(e.target.value)}
                      aria-label="Filtrer les lignes Prix Manar par profil"
                    >
                      <option value={MANARR_PROFIL_TOUS}>
                        {manarrTousProfilsFichierAcceptable
                          ? "🟢 "
                          : prixManarrRows.length > 0
                            ? "🔴 "
                            : ""}
                        Tous les profils ({prixManarrRows.length})
                      </option>
                      {manarrProfilOptions.map((p) => (
                        <option key={p} value={p}>
                          {manarrProfilToutAcceptable.get(p) ? "🟢 " : "🔴 "}
                          {p} ({manarrProfilCounts.get(p) ?? 0})
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <p className="phase-help" style={{ margin: "0.35rem 0 0.5rem", fontSize: "0.88rem" }}>
                  🟢 = profil pour lequel <strong>toutes</strong> les lignes sont « Acceptable » (source écart). 🔴 =
                  au moins une ligne est autre chose (« À corriger » ou autre). Sur « Tous les profils », 🟢 si tout le
                  fichier est acceptable, sinon 🔴.
                </p>
                {manarrProfilFilter !== MANARR_PROFIL_TOUS && (
                  <p className="phase-help" style={{ margin: "0.35rem 0 0.5rem" }}>
                    Affichage filtré :{" "}
                    <strong>
                      {manarrFilteredRows.length} / {prixManarrRows.length}
                    </strong>{" "}
                    ligne(s) pour le profil sélectionné.
                  </p>
                )}
                <table className="data-table" style={{ minWidth: 420 }}>
                  <thead>
                    <tr>
                      <th>Titre</th>
                      <th>Date</th>
                      <th>Valo (fichier)</th>
                      <th>Prix arrondi (moteur)</th>
                      <th style={{ whiteSpace: "nowrap" }}>
                        <span
                          style={{
                            display: "inline-flex",
                            alignItems: "center",
                            gap: "0.25rem",
                            verticalAlign: "middle",
                          }}
                        >
                          Écart
                          <button
                            type="button"
                            onClick={() =>
                              setManarrEcartSort((s) =>
                                s === "none" ? "desc" : s === "desc" ? "asc" : "none"
                              )
                            }
                            title={
                              manarrEcartSort === "none"
                                ? "Trier par écart (du plus grand au plus petit)"
                                : manarrEcartSort === "desc"
                                  ? "Trier par écart (du plus petit au plus grand)"
                                  : "Revenir à l’ordre du fichier (sans tri sur l’écart)"
                            }
                            aria-label={
                              manarrEcartSort === "none"
                                ? "Trier le tableau par écart décroissant"
                                : manarrEcartSort === "desc"
                                  ? "Trier le tableau par écart croissant"
                                  : "Afficher les lignes dans l’ordre du fichier"
                            }
                            style={{
                              display: "inline-flex",
                              alignItems: "center",
                              justifyContent: "center",
                              padding: "1px 3px",
                              margin: 0,
                              border: "1px solid var(--wg-border)",
                              borderRadius: 4,
                              background:
                                manarrEcartSort === "none" ? "transparent" : "var(--wafa-beige)",
                              cursor: "pointer",
                              lineHeight: 0,
                            }}
                          >
                            <ManarrEcartSortGlyph mode={manarrEcartSort} />
                          </button>
                        </span>
                      </th>
                      <th>Source écart</th>
                    </tr>
                  </thead>
                  <tbody>
                    {manarrDisplayRows.length === 0 ? (
                      <tr>
                        <td colSpan={6} style={{ color: "var(--wg-muted)", fontStyle: "italic" }}>
                          Aucune ligne pour ce profil.
                        </td>
                      </tr>
                    ) : (
                      manarrDisplayRows.map((r, i) => {
                        const st = manarrLineStatus(r.source_ecart);
                        const srcColor =
                          st === "corriger"
                            ? "#b91c1c"
                            : st === "acceptable"
                              ? "var(--wg-muted)"
                              : "#78716c";
                        const srcWeight = st === "corriger" ? 700 : 500;
                        return (
                          <tr key={`prix-manarr-${r.titre}-${i}-${manarrProfilFilter}`}>
                            <td>{r.titre}</td>
                            <td>{r.date ?? ""}</td>
                            <td>
                              {r.valo == null
                                ? ""
                                : Number(r.valo).toLocaleString("fr-FR", {
                                    minimumFractionDigits: 2,
                                    maximumFractionDigits: 2,
                                  })}
                            </td>
                            <td>
                              {r.prix_arrondi == null
                                ? ""
                                : Number(r.prix_arrondi).toLocaleString("fr-FR", {
                                    minimumFractionDigits: 2,
                                    maximumFractionDigits: 2,
                                  })}
                            </td>
                            <td>
                              {r.ecart_prix_arrondi_valo == null
                                ? ""
                                : Number(r.ecart_prix_arrondi_valo).toLocaleString("fr-FR", {
                                    minimumFractionDigits: 2,
                                    maximumFractionDigits: 2,
                                  })}
                            </td>
                            <td
                              style={{
                                color: srcColor,
                                fontWeight: srcWeight,
                              }}
                            >
                              {r.source_ecart ?? ""}
                            </td>
                          </tr>
                        );
                      })
                    )}
                  </tbody>
                </table>

                <div
                  className="manarr-stats-block"
                  style={{
                    marginTop: "1rem",
                    padding: "1rem",
                    background: "var(--wg-card)",
                    border: "1px solid var(--wg-border)",
                    borderRadius: 10,
                  }}
                >
                  <h4
                    style={{
                      margin: "0 0 0.75rem",
                      fontSize: "0.95rem",
                      fontWeight: 700,
                      color: "var(--wg-text)",
                    }}
                  >
                    Synthèse fichier (filtre actif)
                  </h4>
                  <div
                    style={{
                      display: "grid",
                      gridTemplateColumns: "repeat(auto-fill, minmax(148px, 1fr))",
                      gap: "0.65rem",
                      marginBottom: "0.85rem",
                    }}
                  >
                    <div
                      style={{
                        padding: "0.55rem 0.65rem",
                        borderRadius: 8,
                        border: "1px solid var(--wg-border)",
                        background: "var(--wg-page)",
                      }}
                    >
                      <div style={{ fontSize: "0.72rem", color: "var(--wg-muted)", marginBottom: "0.2rem" }}>
                        Lignes affichées
                      </div>
                      <div style={{ fontSize: "1.15rem", fontWeight: 700, color: "var(--wg-text)" }}>
                        {manarrStats.total}
                      </div>
                    </div>
                    <div
                      style={{
                        padding: "0.55rem 0.65rem",
                        borderRadius: 8,
                        border: "1px solid var(--wg-border)",
                        background: "var(--wg-page)",
                      }}
                    >
                      <div style={{ fontSize: "0.72rem", color: "var(--wg-muted)", marginBottom: "0.2rem" }}>
                        Acceptable
                      </div>
                      <div style={{ fontSize: "1.05rem", fontWeight: 700, color: "#15803d" }}>
                        {manarrStats.ok}{" "}
                        <span style={{ fontWeight: 500, fontSize: "0.88rem" }}>({manarrStats.pctOk} %)</span>
                      </div>
                    </div>
                    <div
                      style={{
                        padding: "0.55rem 0.65rem",
                        borderRadius: 8,
                        border: "1px solid var(--wg-border)",
                        background: "var(--wg-page)",
                      }}
                    >
                      <div style={{ fontSize: "0.72rem", color: "var(--wg-muted)", marginBottom: "0.2rem" }}>
                        À corriger
                      </div>
                      <div style={{ fontSize: "1.05rem", fontWeight: 700, color: "#b91c1c" }}>
                        {manarrStats.corr}{" "}
                        <span style={{ fontWeight: 500, fontSize: "0.88rem" }}>({manarrStats.pctCorr} %)</span>
                      </div>
                    </div>
                    <div
                      style={{
                        padding: "0.55rem 0.65rem",
                        borderRadius: 8,
                        border: "1px solid var(--wg-border)",
                        background: "var(--wg-page)",
                      }}
                    >
                      <div style={{ fontSize: "0.72rem", color: "var(--wg-muted)", marginBottom: "0.2rem" }}>
                        Autre / inconnu
                      </div>
                      <div style={{ fontSize: "1.05rem", fontWeight: 700, color: "#78716c" }}>
                        {manarrStats.other}{" "}
                        <span style={{ fontWeight: 500, fontSize: "0.88rem" }}>({manarrStats.pctOther} %)</span>
                      </div>
                    </div>
                  </div>

                  <div style={{ display: "flex", flexWrap: "wrap", gap: "1rem", alignItems: "center" }}>
                    <div style={{ flex: "1 1 220px", minHeight: 200 }}>
                      {manarrStats.total > 0 && manarrChartData.length > 0 ? (
                        <ResponsiveContainer width="100%" height={200}>
                          <PieChart>
                            <Pie
                              data={manarrChartData}
                              dataKey="value"
                              nameKey="name"
                              cx="50%"
                              cy="50%"
                              innerRadius={54}
                              outerRadius={78}
                              paddingAngle={2}
                              labelLine={false}
                              label={({ name, percent }) =>
                                `${name}: ${((percent ?? 0) * 100).toFixed(0)} %`
                              }
                            >
                              {manarrChartData.map((entry, index) => (
                                <Cell key={`manarr-pie-${index}`} fill={entry.fill} stroke="var(--wg-card)" strokeWidth={2} />
                              ))}
                            </Pie>
                            <Tooltip
                              formatter={(value: number) => [`${value} ligne(s)`, ""]}
                              contentStyle={{
                                borderRadius: 8,
                                border: "1px solid var(--wg-border)",
                              }}
                            />
                            <Legend />
                          </PieChart>
                        </ResponsiveContainer>
                      ) : (
                        <p className="phase-help" style={{ margin: 0 }}>
                          Pas assez de données pour le graphique (aucune ligne affichée ou effectifs nuls).
                        </p>
                      )}
                    </div>
                    <div style={{ flex: "1 1 200px", minWidth: 180 }}>
                      <p className="phase-help" style={{ margin: 0, lineHeight: 1.55 }}>
                        Statuts dérivés du texte « Source écart » : <strong>acceptable</strong> si la chaîne contient
                        « acceptable » ; <strong>à corriger</strong> si elle contient « a corriger » ou « à corriger » ;
                        sinon <strong>autre / inconnu</strong>. Les pourcentages portent sur les{" "}
                        <strong>{manarrStats.total}</strong> ligne(s) visibles dans le tableau ci-dessus.
                      </p>
                    </div>
                  </div>
                </div>
              </article>
            )}

          </>
        )}

        {marcheRows.length > 200 && (
          <details className="pricing-cashflows-details">
            <summary>Aperçu limité à 200 lignes dans l&apos;UI (export Excel complet généré côté backend)</summary>
            <table className="data-table">
              <thead>
                <tr>
                  <th>CODE</th>
                  <th>Taux facial utilisé (coupon couru)</th>
                  <th>Description</th>
                  <th>{MARCHE_KEY_DATE_ECHEANCE}</th>
                </tr>
              </thead>
              <tbody>
                {marcheRows.slice(200, 240).map((r, i) => (
                  <tr key={`more-${r.CODE}-${i}`}>
                    <td>{String(r.CODE)}</td>
                    <td>{`${Number(r["Taux facial utilisé (coupon couru)"] ?? r.TAUX ?? 0).toLocaleString("fr-FR", {
                      minimumFractionDigits: 2,
                      maximumFractionDigits: 4,
                    })}%`}</td>
                    <td>{r.description}</td>
                    <td>{String((r as Record<string, unknown>)[MARCHE_KEY_DATE_ECHEANCE] ?? "")}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        )}
      </section>
            </>
          ) : activeWorkspace === "portfolio" ? (
            <section className="portfolio-section">
              <div className="portfolio-launch-card">
                <h2>Gestion de portefeuille</h2>
                <p className="portfolio-mbi-intro">
                  Construction automatique d’indices obligataires inspirés du MBI : les titres du référentiel actif sont classés
                  selon la maturité résiduelle (en années, à partir des jours jusqu’à l’échéance).
                </p>
                <ul className="portfolio-mbi-rules">
                  <li>
                    <strong>MBI Monétaire</strong> : maturité résiduelle &lt; 1 an
                  </li>
                  <li>
                    <strong>MBI CT</strong> : entre 1 et 3 ans
                  </li>
                  <li>
                    <strong>MBI MT</strong> : entre 3 et 5 ans
                  </li>
                  <li>
                    <strong>MBI MLT</strong> : entre 5 et 10 ans
                  </li>
                  <li>
                    <strong>MBI LT</strong> : &gt; 10 ans
                  </li>
                  <li>
                    <strong>MBI Global</strong> : toutes les obligations éligibles
                  </li>
                </ul>
                <div className="bond-form-grid">
                  <label>
                    <span>Date de valorisation</span>
                    <input type="date" value={portfolioDate} onChange={(e) => setPortfolioDate(e.target.value)} />
                  </label>
                  <label>
                    <span>Date début</span>
                    <input type="date" value={portfolioStartDate} onChange={(e) => setPortfolioStartDate(e.target.value)} />
                  </label>
                  <label>
                    <span>Date fin</span>
                    <input type="date" value={portfolioEndDate} onChange={(e) => setPortfolioEndDate(e.target.value)} />
                  </label>
                  <label>
                    <span>Fréquence</span>
                    <select value={portfolioFrequency} onChange={(e) => setPortfolioFrequency(e.target.value as PortfolioFrequency)}>
                      {PORTFOLIO_FREQUENCY_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>{o.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Indice MBI (filtrage par maturité résiduelle)</span>
                    <select
                      value={portfolioMbiTranche}
                      onChange={(e) => setPortfolioMbiTranche(e.target.value as PortfolioMbiTranche)}
                    >
                      {PORTFOLIO_MBI_OPTIONS.map((o) => (
                        <option key={o.value} value={o.value}>
                          {o.label}
                        </option>
                      ))}
                    </select>
                  </label>
                </div>
                <button type="button" className="phase-action-btn" onClick={runPortfolioValuation} disabled={portfolioLoading}>
                  {portfolioLoading
                    ? "Calcul portefeuille..."
                    : portfolioMode === "history"
                      ? "Lancer analyse historique"
                      : "Calculer snapshot"}
                </button>
                <p className="portfolio-mbi-intro">
                  Mode actif : <strong>{portfolioMode === "history" ? "Analyse historique / performance" : "Photo de portefeuille"}</strong>.
                  Le snapshot ne calcule pas rendement historique, volatilité, Sharpe, VaR ni drawdown.
                </p>
                {portfolioError && <div className="portfolio-error">{portfolioError}</div>}
              </div>

              {portfolioData && (
                <>
                  <article className="portfolio-panel portfolio-summary-panel">
                    <div className="portfolio-panel-header">
                      <div>
                        <h3>
                          {portfolioData.portfolio_name} — {formatIsoDateFr(portfolioData.date_valo)}
                        </h3>
                        <p className="portfolio-summary-intro">
                          Indicateurs agrégés de l&apos;indice (poids et métriques par obligation).
                        </p>
                      </div>
                    </div>
                    <div className="portfolio-kpi-grid">
                      <article className="portfolio-kpi">
                        <span>Valeur totale portefeuille</span>
                        <strong>{formatAmount(portfolioData.summary.total_market_value, 2)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Nombre d&apos;obligations</span>
                        <strong>{portfolioData.summary.number_of_bonds}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Nombre d&apos;émetteurs</span>
                        <strong>{portfolioData.summary.number_of_issuers ?? "—"}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>YTM moyen pondéré</span>
                        <strong>{formatPctFromDecimal(portfolioData.summary.weighted_ytm, 3)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Duration portefeuille</span>
                        <strong>{formatAmount(portfolioData.summary.weighted_duration, 2)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Sensibilité portefeuille</span>
                        <strong>{formatAmount(portfolioData.summary.weighted_sensibilite, 2)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Convexité portefeuille</span>
                        <strong>{formatAmount(portfolioData.summary.weighted_convexite, 2)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Spread moyen pondéré</span>
                        <strong>{formatPctNumber(portfolioData.summary.weighted_spread, 3)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Coupon couru total</span>
                        <strong>{formatAmount(portfolioData.summary.total_accrued_coupon, 2)}</strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>DV01 / PVBP portefeuille</span>
                        <strong>
                          {formatAmount(Number(portfolioData.summary.portfolio_dv01 ?? Number.NaN), 2)}
                        </strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Poids max ligne</span>
                        <strong>
                          {formatPctFromDecimal(
                            Number(portfolioData.summary.max_position_weight ?? Number.NaN),
                            2,
                          )}
                        </strong>
                      </article>
                      <article className="portfolio-kpi">
                        <span>Top émetteur</span>
                        <strong>{portfolioData.summary.top_issuer || "—"}</strong>
                        <em>
                          {formatPctFromDecimal(
                            Number(portfolioData.summary.top_issuer_weight ?? Number.NaN),
                            2,
                          )}
                        </em>
                      </article>
                    </div>
                  </article>

                  <article className="portfolio-panel">
                    <div className="portfolio-panel-header">
                      <h3>Analytics snapshot</h3>
                      <span>Poids, maturité, secteur et contribution DV01</span>
                    </div>
                    <div className="portfolio-risk-grid">
                      <ResponsiveContainer width="100%" height={260}>
                        <PieChart>
                          <Pie data={portfolioAllocationRows.maturity} dataKey="weight" nameKey="name" outerRadius={86} label>
                            {portfolioAllocationRows.maturity.map((_, i) => (
                              <Cell key={`mat-${i}`} fill={["#143a66", "#c84f1a", "#d99a1e", "#2f6f73", "#6b7280"][i % 5]} />
                            ))}
                          </Pie>
                          <Tooltip formatter={(v: number) => formatPctFromDecimal(Number(v), 2)} />
                          <Legend />
                        </PieChart>
                      </ResponsiveContainer>
                      <ResponsiveContainer width="100%" height={260}>
                        <BarChart data={portfolioAllocationRows.sectors}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="name" hide />
                          <YAxis tickFormatter={(v) => `${(Number(v) * 100).toFixed(0)}%`} />
                          <Tooltip formatter={(v: number) => formatPctFromDecimal(Number(v), 2)} />
                          <Bar dataKey="weight" fill="#143a66" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </article>

                  <article className="portfolio-panel">
                    <div className="portfolio-panel-header">
                      <h3>Positions</h3>
                      <span>
                        {portfolioPositionsSorted.length} ligne
                        {portfolioPositionsSorted.length > 1 ? "s" : ""} — tri par poids décroissant
                        {portfolioPositionsSorted.length > 10
                          ? " · faites défiler pour voir les autres"
                          : ""}
                      </span>
                    </div>
                    <div className="portfolio-table-wrap portfolio-positions-scroll">
                      <table className="data-table portfolio-table">
                        <thead>
                          <tr>
                            <th>Code</th>
                            <th>Description</th>
                            <th>Émetteur</th>
                            <th>Quantité</th>
                            <th>Prix</th>
                            <th>Valeur marché</th>
                            <th>Poids</th>
                            <th>YTM</th>
                            <th>Duration</th>
                            <th>Sensibilité</th>
                            <th>Convexité</th>
                            <th>Spread</th>
                            <th>Maturité résiduelle</th>
                            <th>Coupon couru</th>
                            <th>Date échéance</th>
                          </tr>
                        </thead>
                        <tbody>
                          {portfolioPositionsSorted.map((p) => (
                            <tr key={p.code}>
                              <td>{p.code}</td>
                              <td>{p.description}</td>
                              <td>{p.emetteur}</td>
                              <td>{formatAmount(p.quantite, 0)}</td>
                              <td>{formatAmount(p.price, 6)}</td>
                              <td>{formatAmount(p.market_value, 2)}</td>
                              <td>{formatPctFromDecimal(p.weight, 2)}</td>
                              <td>{formatPctFromDecimal(p.ytm, 3)}</td>
                              <td>{formatAmount(p.duration, 3)}</td>
                              <td>{formatAmount(p.sensibilite, 3)}</td>
                              <td>{formatAmount(p.convexite, 2)}</td>
                              <td>{formatPctNumber(p.spread, 3)}</td>
                              <td>{formatAmount(p.maturite_residuelle, 0)}</td>
                              <td>{formatAmount(p.coupon_couru, 4)}</td>
                              <td>{p.date_echeance}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </div>
                  </article>

                  <article className="portfolio-panel portfolio-risk-panel">
                    <div className="portfolio-risk-contrib">
                      <h4>Contribution au risque</h4>
                      <div className="portfolio-table-wrap">
                        <table className="data-table portfolio-risk-table">
                          <thead>
                            <tr>
                              <th>Code</th>
                              <th>Description</th>
                              <th>Émetteur</th>
                              <th>Poids</th>
                              <th>Sensibilité</th>
                              <th>Valeur marché</th>
                              <th>DV01</th>
                              <th>Contribution DV01 %</th>
                            </tr>
                          </thead>
                          <tbody>
                            {portfolioRiskContributions.map((p) => (
                              <tr key={`risk-${p.code}`}>
                                <td>{p.code}</td>
                                <td>{p.description}</td>
                                <td>{p.emetteur}</td>
                                <td>{formatPctFromDecimal(p.weight, 2)}</td>
                                <td>{formatAmount(p.sensibilite, 2)}</td>
                                <td>{formatAmount(p.market_value, 2)}</td>
                                <td>{formatAmount(p.dv01, 2)}</td>
                                <td>{formatPctFromDecimal(p.contribution_dv01_pct, 2)}</td>
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </div>
                  </article>

                  {portfolioData.errors.length > 0 && (
                    <article className="portfolio-panel portfolio-errors-panel">
                      <h3>Codes non valorisés</h3>
                      <table className="data-table">
                        <thead>
                          <tr>
                            <th>Code</th>
                            <th>Raison</th>
                          </tr>
                        </thead>
                        <tbody>
                          {portfolioData.errors.map((e) => (
                            <tr key={`${e.code}-${e.reason}`}>
                              <td>{e.code}</td>
                              <td>{e.reason}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </article>
                  )}
                </>
              )}
              {portfolioHistory && (
                <>
                  <article className="portfolio-panel portfolio-summary-panel">
                    <div className="portfolio-panel-header">
                      <div>
                        <h3>{portfolioHistory.portfolio_name} — analyse historique</h3>
                        <p className="portfolio-summary-intro">
                          {formatIsoDateFr(portfolioHistory.date_debut)} au {formatIsoDateFr(portfolioHistory.date_fin)} · {portfolioHistory.frequence}
                        </p>
                      </div>
                    </div>
                    <div className="portfolio-kpi-grid">
                      <article className="portfolio-kpi"><span>Performance cumulée</span><strong>{formatPctFromDecimal(portfolioHistory.summary.performance_cumulee, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>Performance annualisée</span><strong>{formatPctFromDecimal(portfolioHistory.summary.performance_annualisee, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>Volatilité annualisée</span><strong>{formatPctFromDecimal(portfolioHistory.summary.volatilite_annualisee, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>Sharpe Ratio</span><strong>{formatAmount(portfolioHistory.summary.sharpe_ratio, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>Max Drawdown</span><strong>{formatPctFromDecimal(portfolioHistory.summary.max_drawdown, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>PnL cumulé</span><strong>{formatAmount(portfolioHistory.summary.pnl_total, 2)}</strong></article>
                      <article className="portfolio-kpi"><span>VaR 95%</span><strong>{formatPctFromDecimal(portfolioHistory.summary.var["95"]?.pct, 2)}</strong><em>{formatAmount(portfolioHistory.summary.var["95"]?.amount, 2)} MAD</em></article>
                      <article className="portfolio-kpi"><span>Observations pricing</span><strong>{portfolioHistory.summary.observations}</strong><em>{portfolioHistory.summary.display_observations ?? portfolioHistory.series.length} affichÃ©es</em></article>
                      <article className="portfolio-kpi"><span>Dates disponibles</span><strong>{portfolioHistory.summary.dates_available ?? portfolioHistory.summary.observations}</strong><em>{portfolioHistory.summary.dates_ignored ?? portfolioHistory.errors.length} ignorÃ©es</em></article>
                    </div>
                  </article>

                  <article className="portfolio-panel">
                    <div className="portfolio-panel-header">
                      <h3>Performance & risque</h3>
                      <span>NAV, rendement cumulé, duration, DV01, YTM et drawdown</span>
                    </div>
                    <div className="portfolio-risk-grid">
                      <ResponsiveContainer width="100%" height={420}>
                        <LineChart data={historyChartRows}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="dateLabel" minTickGap={24} />
                          <YAxis />
                          <Tooltip />
                          <Legend />
                          <Line type="monotone" dataKey="index_base_100" name="Indice base 100" stroke="#143a66" dot={false} />
                          <Line type="monotone" dataKey="nav_m" name="NAV (MM MAD)" stroke="#64748b" dot={false} />
                          <Line type="monotone" dataKey="cumulative_pct" name="Rendement cumulé %" stroke="#c84f1a" dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                      <ResponsiveContainer width="100%" height={320}>
                        <LineChart data={historyChartRows}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="dateLabel" minTickGap={24} />
                          <YAxis />
                          <Tooltip />
                          <Legend />
                          <Line type="monotone" dataKey="duration" name="Duration" stroke="#143a66" dot={false} />
                          <Line type="monotone" dataKey="dv01" name="DV01" stroke="#d99a1e" dot={false} />
                          <Line type="monotone" dataKey="ytm_pct" name="YTM %" stroke="#2f6f73" dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                      <ResponsiveContainer width="100%" height={340}>
                        <LineChart data={historyChartRows}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="dateLabel" minTickGap={24} />
                          <YAxis />
                          <Tooltip />
                          <Line type="monotone" dataKey="drawdown_pct" name="Drawdown %" stroke="#b91c1c" dot={false} />
                        </LineChart>
                      </ResponsiveContainer>
                      <ResponsiveContainer width="100%" height={360}>
                        <BarChart data={returnHistogramRows}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="bucket" hide />
                          <YAxis />
                          <Tooltip />
                          <Bar dataKey="count" name="Rendements" fill="#143a66" />
                        </BarChart>
                      </ResponsiveContainer>
                      <ResponsiveContainer width="100%" height={320}>
                        <BarChart data={historyChartRows}>
                          <CartesianGrid strokeDasharray="3 3" />
                          <XAxis dataKey="dateLabel" minTickGap={24} />
                          <YAxis />
                          <Tooltip />
                          <Legend />
                          <Bar dataKey="entries_count" name="EntrÃ©es" fill="#15803d" />
                          <Bar dataKey="exits_count" name="Sorties" fill="#b91c1c" />
                          <Bar dataKey="turnover_pct" name="Turnover %" fill="#d99a1e" />
                        </BarChart>
                      </ResponsiveContainer>
                    </div>
                  </article>

                  {portfolioHistory.errors.length > 0 && (
                    <article className="portfolio-panel portfolio-errors-panel">
                      <h3>Dates ignorées</h3>
                      <table className="data-table">
                        <thead><tr><th>Date</th><th>Raison</th></tr></thead>
                        <tbody>
                          {portfolioHistory.errors.slice(0, 20).map((e) => (
                            <tr key={`${e.date}-${e.reason}`}><td>{formatIsoDateFr(e.date)}</td><td>{e.reason}</td></tr>
                          ))}
                        </tbody>
                      </table>
                    </article>
                  )}
                </>
              )}
            </section>
          ) : (
            <section className="wg-module-placeholder">
              <h2>Gestion de risque</h2>
              <p>Module à compléter.</p>
            </section>
          )}
        </main>
      </div>
    </div>
  );
}
