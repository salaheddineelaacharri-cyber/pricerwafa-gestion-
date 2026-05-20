export type PillarShort = { maturity_days: number; mm_rate_pct: number };
export type PillarLong = { maturity_days: number; actuarial_rate_pct: number };

export type CurveRequest = {
  short: PillarShort[];
  long: PillarLong[];
  joint_days: number;
  max_days: number;
  step_short: number;
  step_long: number;
  /** AAAA-MM-JJ : ancrage grille échéancier ZC annuel (requis pour /api/curve). */
  zc_schedule_anchor_date?: string | null;
};

/** Tableau échéancier (Maturité, Taux, Année, TauxZC, PXZC, TauxZCActuariel) */
export type ScheduleRow = {
  Maturity_days: number;
  Taux_pct: number;
  Annee: number;
  Taux_ZC_pct: number;
  /** Colonne TauxZC (8 déc.), alignée graphique / API chart.zc_pct */
  Taux_ZC_pct_full?: number;
  PXZC: number | null;
  Taux_ZC_actuariel_pct: number;
};

export type CurveResponse = {
  table: Record<string, number>[];
  chart: {
    maturity_days: number[];
    /** Taux ZC = colonne TauxZC de l’échéancier (maturités fixes, même formules Excel) */
    zc_pct: number[];
    /** Alias histor. = zc_pct (même série) */
    actuarial_pct?: number[];
    quoted_pct: number[];
  };
  schedule_table: ScheduleRow[];
};

export type CurvePillarsFromHistoRequest = {
  date_courbe: string;
  courbe?: string;
};

export type CurvePillarsFromHistoResponse = {
  short: PillarShort[];
  long: PillarLong[];
  /** Dernier pilier CT = joint_long_day ; seuil Excel G2−1 (ex. 325 si G2=326). */
  joint_days?: number;
  joint_long_day?: number;
  points?: { maturity_days: number; rate_pct: number; segment: "CT" | "LT" }[];
  split_maturity_days?: number;
  max_maturity_days?: number;
  date_requested: string;
  date_used: string;
  courbe: string;
  source_file: string;
};

export type BondRequest = {
  curve: CurveRequest;
  nominal: number;
  coupon_pct: number;
  maturity_years: number;
  frequency: number;
};

export type BondResponse = {
  cashflows: Record<string, number>[];
  metrics: { Metric: string; Value: number }[];
};

/** Lignes attendues pour les tableaux « Pricing obligataire » (à remplir par votre future API). */
export type MarcheValorizeRow = {
  CODE: number | string;
  "Taux facial utilisé (coupon couru)"?: number;
  description: string;
  Description?: string;
  TAUX: number;
  "Date d'échéance": string;
  "Date d'émission"?: string;
  "Maturité résiduelle (jours)": number;
  "Coupon couru": number;
  "Prix dirty": number;
  "Prix clean": number;
  "Prix arrondi": number;
  "Prix MR"?: number | null;
  "Ecart Prix arrondi - Prix MR"?: number | null;
  "Rendement (YTM)": number;
  "Duration titre": number;
  "Sensibilité": number;
  "Convexité": number;
  Nominal?: number;
  Spread?: number;
  /** Présent côté API : ``ATP`` si la ligne n’a pas été recalculée par la grille amortissement. */
  moteur_prix?: string;
};

/**
 * Prix clean NPV pour la colonne « Prix arrondi » : lit la réponse JSON telle quelle.
 * Si « Prix arrondi » et « Prix clean » divergent (anomalie / cache partiel), on retient
 * « Prix clean » (aligné verrou grille côté API).
 */
export function prixValoAfficheMarche(r: MarcheValorizeRow): number {
  const o = r as Record<string, unknown>;
  const n = (v: unknown): number => {
    if (typeof v === "number" && Number.isFinite(v)) return v;
    if (typeof v === "string" && v.trim()) {
      const t = v.replace(/\s/g, "").replace(",", ".");
      const x = Number(t);
      return Number.isFinite(x) ? x : NaN;
    }
    return NaN;
  };
  const pa = n(o["Prix arrondi"]);
  const pc = n(o["Prix clean"]);
  if (Number.isFinite(pa) && Number.isFinite(pc) && Math.abs(pa - pc) > 1e-5) {
    return pc;
  }
  if (Number.isFinite(pa)) return pa;
  if (Number.isFinite(pc)) return pc;
  return 0;
}

export type MarcheValorizeRequestBody = {
  valuation_date?: string;
  code_maroclear?: string;
  courbe_zc_py?: string | null;
  excel_xlsx?: string | null;
  /** Mêmes piliers que « Tracer la courbe » : taux secondaire BAM + spread (sinon fichier courbe_zc.py seul). */
  curve?: CurveRequest;
  /** Recalcule feuil1_titres (ordre Feuil1) avec Prix arrondi / MR / écart moteur pour chaque code. */
  feuil1_pricer_tous?: boolean;
  prix_manarr_pricer_tous?: boolean;
};

/** Tableau type AWB : amortissement / flux / actualisation (obligations avec feuille echeancier_Titre). */
export type AmortissementTableRow = {
  label: string;
  values: (number | null)[];
  /** pct 3 dec. | pct5 | amount4 (ZC) | amount5 (REV) | dec3 | dec10 (durée Excel H479) | dec12 (legacy) */
  format?: "pct" | "pct5" | "amount4" | "amount5" | "dec3" | "dec10" | "dec12";
};

export type AmortissementTable = {
  type?: string;
  code: string | number;
  description: string;
  note?: string | null;
  taux_coupon_pct: number;
  /** Dates ISO (AAAA-MM-JJ), une par colonne de versement */
  columns: string[];
  rows: AmortissementTableRow[];
  /** Prix clean = Σ PV pleine précision (moteur) ; l’UI « Prix arrondi » utilise ``prixValoAfficheMarche`` sur la réponse (``Prix arrondi`` / ``Prix clean``). */
  prix_somme_flux_actualises?: number | null;
  /** Indique si l’échéancier pilote le prix côté moteur (diagnostic). */
  prix_clean_pilote_par_echeancier?: boolean;
  prix_actualise?: number | null;
  duration_macaulay?: number | null;
  /** Obligation révisable : prix linéaire ; ligne courbe « Taux AA » ou « Taux ZC » selon référentiel METHODE_VALO. */
  pricing_rev_bond?: boolean;
  /** Valeur lue sur le référentiel (ex. AA, ZC, MN). */
  methode_valo?: string | null;
  /** Si true : METHODE_VALO ZC → courbe ZC (échéancier / fichier) ; sinon courbe secondaire BAM (Taux AA). */
  courbe_zc_active?: boolean;
  /** Date utilisée pour le tableau (API ; titres REV : peut être DATE_VALO référentiel). */
  date_valorisation_utilisee_iso?: string | null;
  /** Nominal servant au tableau (chaîne « capital restant » type Excel AWB). */
  nominal_reference?: number | null;
  /** Doit être ``excel-arrondi-h478-h47912-h482-2026-04-12`` si le calcul est à jour. */
  amort_engine_id?: string | null;
};

export type MarcheValorizeDiagnostic = {
  colonnes_feuille: string[];
  colonnes_utilisees: Record<string, string | null>;
  colonne_code_fichier?: string | null;
  nb_lignes_fichier_total?: number;
  nb_lignes_apres_filtre_code?: number;
  nb_lignes_lues: number;
  nb_lignes_valorisees: number;
  nb_lignes_affichees: number;
  nb_lignes_echeance_depassee?: number;
  filtre_code: string | null;
  filtre_code_normalise?: string | null;
  astuce_filtre?: string | null;
  /** Nombre de grilles échéancier renvoyées (0 = rien sous Synthèse titre) */
  amortissement_tables_count?: number;
  amortissement_error?: string | null;
  feuilles_classeur?: string[];
  feuille_echeancier_detectee?: string | null;
  feuille_referentiel_detectee?: string | null;
  /** Doit être ``excel-amm-h478-h47910-h482dec5-metvalo-zcpow-fper-trireel-2026-04-15`` si le backend est à jour. */
  amort_engine_id?: string | null;
  amort_schedule_module?: string | null;
  /** Feuille Excel lue pour la liste portefeuille (Feuil1 / Feuille1 / Sheet1). */
  feuil1_feuille?: string | null;
  nb_feuil1_titres?: number;
};

/** Ligne feuille Feuil1 : ordre identique au fichier (titre = code). */
export type Feuil1TitreRow = {
  titre: string;
  date: string | null;
  /** Prix arrondi moteur (champ JSON ``valo`` ; rempli après « Valoriser toutes les obligations »). */
  valo: number | null;
  /** Référence Manar / marché (colonne PRICE ou équivalent). */
  prix_mr?: number | null;
  /** Écart : colonne Excel si présente, sinon prix_mr − valo (à 2 déc.). */
  ecart?: number | null;
};

export type PrixManarrRow = {
  titre: string;
  date: string | null;
  /** Montant lu sur le classeur Prix Manar (colonne « valo »). */
  valo: number | null;
  /** Prix clean moteur (table Valorisation), rempli si « valoriser tout » ou recalcul. */
  prix_arrondi?: number | null;
  ecart_prix_arrondi_valo?: number | null;
  source_prix_arrondi?: string | null;
  source_ecart?: string | null;
  ecart_a_corriger?: boolean;
  profil_metier?: string | null;
};

export type MarcheValorizeResponse = {
  rows: MarcheValorizeRow[];
  nb_lignes: number;
  fichier?: string;
  message?: string | null;
  diagnostic?: MarcheValorizeDiagnostic;
  amortissement_tables?: AmortissementTable[];
  /** Liste lue sur Feuil1 (toutes les obligations listées dans le classeur). */
  feuil1_titres?: Feuil1TitreRow[];
  prix_manarr?: PrixManarrRow[];
};

/** Réponse + en-tête HTTP (preuve que le proxy parle au bon Uvicorn). */
export type MarcheValorizeResult = {
  data: MarcheValorizeResponse;
  amortEngineIdHeader: string | null;
};

export type PortfolioPosition = {
  code: string;
  description: string;
  emetteur: string;
  secteur?: string;
  quantite: number;
  nominal: number;
  price: number;
  market_value: number;
  weight: number;
  ytm: number;
  duration: number;
  sensibilite: number;
  convexite: number;
  spread: number;
  maturite_residuelle: number;
  coupon_couru: number;
  dv01?: number | null;
  date_echeance: string;
};

export type PortfolioMbiTranche = "global" | "monetaire" | "ct" | "mt" | "mlt" | "lt";

export type PortfolioValuationResponse = {
  mode?: "snapshot";
  portfolio_name: string;
  mbi_tranche?: PortfolioMbiTranche | string;
  date_valo: string;
  summary: {
    total_market_value: number;
    number_of_bonds: number;
    weighted_ytm: number;
    weighted_duration: number;
    weighted_sensibilite: number;
    weighted_convexite: number;
    weighted_spread: number;
    total_accrued_coupon: number;
    portfolio_dv01?: number | null;
    max_position_weight?: number | null;
    top_issuer?: string | null;
    top_issuer_weight?: number | null;
    number_of_issuers?: number | null;
  };
  allocations?: {
    by_issuer: { name: string; weight: number }[];
    by_sector: { name: string; weight: number }[];
    by_maturity: { name: string; weight: number }[];
    risk_contribution: {
      code: string;
      description: string;
      emetteur: string;
      weight: number;
      dv01: number;
      contribution_dv01_pct: number;
    }[];
  };
  positions: PortfolioPosition[];
  errors: { code: string; reason: string }[];
};

export type PortfolioFrequency = "daily" | "weekly" | "monthly" | "quarterly" | "yearly";

export type PortfolioHistoryPoint = {
  date: string;
  nav: number;
  raw_nav?: number;
  index_base_100?: number;
  return: number | null;
  pnl: number | null;
  pnl_cumule?: number;
  cumulative_return: number;
  drawdown?: number;
  duration: number;
  sensibilite: number;
  convexite: number;
  ytm: number;
  spread: number;
  dv01: number;
  number_of_bonds: number;
  number_of_issuers?: number;
  entries_count?: number;
  exits_count?: number;
  entries_codes?: string[];
  exits_codes?: string[];
  universe_turnover?: number;
};

export type PortfolioHistoryResponse = {
  mode: "history";
  portfolio_name: string;
  mbi_tranche: PortfolioMbiTranche | string;
  date_debut: string;
  date_fin: string;
  frequence: PortfolioFrequency | string;
  source_dates?: string;
  summary: {
    observations: number;
    display_observations?: number;
    dates_available?: number;
    dates_ignored?: number;
    actual_start_date?: string | null;
    actual_end_date?: string | null;
    statistics_available?: boolean;
    quality_warning?: string | null;
    calculation_method?: string;
    rebalanced?: boolean;
    performance_cumulee: number;
    performance_annualisee: number | null;
    volatilite_annualisee: number | null;
    sharpe_ratio: number | null;
    max_drawdown: number;
    tracking_error: number;
    information_ratio: number;
    pnl_total: number;
    var: Record<string, { pct: number | null; amount: number | null }>;
  };
  series: PortfolioHistoryPoint[];
  daily_series?: PortfolioHistoryPoint[];
  returns: number[];
  display_returns?: number[];
  errors: { date: string; reason: string }[];
};

const jsonHeaders = { "Content-Type": "application/json" };

export async function postCurve(body: CurveRequest): Promise<CurveResponse> {
  const r = await fetch("/api/curve", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? r.statusText);
  }
  return r.json();
}

export async function postCurvePillarsFromHisto(
  body: CurvePillarsFromHistoRequest
): Promise<CurvePillarsFromHistoResponse> {
  const r = await fetch("/api/curve/pillars-from-histo", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? r.statusText);
  }
  return r.json();
}

export async function postBond(body: BondRequest): Promise<BondResponse> {
  const r = await fetch("/api/bond", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(body),
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    throw new Error((err as { detail?: string }).detail ?? r.statusText);
  }
  return r.json();
}

/** Pricing obligataire : ZC + fichier base titre oblig (data/obligations/base_titre_oblig.xlsx ou racine). */
export async function postMarcheValorize(body: MarcheValorizeRequestBody = {}): Promise<MarcheValorizeResult> {
  const r = await fetch("/api/marche/valorize", {
    method: "POST",
    headers: jsonHeaders,
    body: JSON.stringify(body),
    cache: "no-store",
  });
  const amortEngineIdHeader = r.headers.get("X-Pricer-Amort-Engine-ID");
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    const d = err as { detail?: string | unknown };
    const msg = typeof d.detail === "string" ? d.detail : r.statusText;
    throw new Error(msg);
  }
  const data = (await r.json()) as MarcheValorizeResponse;
  return { data, amortEngineIdHeader };
}

export async function getPortfolioValuation(params: {
  date_valo: string;
  mbi_tranche?: PortfolioMbiTranche;
}): Promise<PortfolioValuationResponse> {
  const qs = new URLSearchParams();
  qs.set("date_valo", params.date_valo);
  qs.set("valuation_date", params.date_valo);
  qs.set("mbi_tranche", params.mbi_tranche ?? "global");
  qs.set("index_type", params.mbi_tranche ?? "global");
  const r = await fetch(`/api/portfolio/valuation?${qs.toString()}`, {
    method: "GET",
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    const d = err as { detail?: string | unknown };
    const msg = typeof d.detail === "string" ? d.detail : r.statusText;
    throw new Error(msg);
  }
  return r.json();
}

export async function getPortfolioHistory(params: {
  date_debut: string;
  date_fin: string;
  mbi_tranche?: PortfolioMbiTranche;
  frequence?: PortfolioFrequency;
}): Promise<PortfolioHistoryResponse> {
  const qs = new URLSearchParams();
  qs.set("date_debut", params.date_debut);
  qs.set("date_fin", params.date_fin);
  qs.set("mbi_tranche", params.mbi_tranche ?? "global");
  qs.set("frequence", params.frequence ?? "monthly");
  qs.set("start_date", params.date_debut);
  qs.set("end_date", params.date_fin);
  qs.set("index_type", params.mbi_tranche ?? "global");
  qs.set("display_frequency", params.frequence ?? "monthly");
  const r = await fetch(`/api/portfolio/history?${qs.toString()}`, {
    method: "GET",
  });
  if (!r.ok) {
    const err = await r.json().catch(() => ({}));
    const d = err as { detail?: string | unknown };
    const msg = typeof d.detail === "string" ? d.detail : r.statusText;
    throw new Error(msg);
  }
  return r.json();
}
