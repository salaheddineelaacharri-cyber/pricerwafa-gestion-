import { useMemo } from "react";
import { CartesianGrid, Legend, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";

type Point = { d: number; r: number };
type Zone = "CT" | "Transition" | "MLT";
type PillarShort = { maturity_days: number; mm_rate_pct: number };
type PillarLong = { maturity_days: number; actuarial_rate_pct: number };

type Row = {
  days: number;
  years: number;
  a: number;
  b: number;
  diffPct: number;
  diffBp: number;
  zone: Zone;
  /** Maturités surlignées comme le bloc orange Excel (piliers LT type 3m–20y). */
  isReferencePilier: boolean;
};

const BASE = 365;
const SEUIL_G2 = 326;

/** Même grille et même ordre que la colonne Excel « Maturity_days » (ne pas trier). */
const MATURITIES_EXCEL_ORDER: number[] = [
  1, 50, 100, 150, 200,
  91, 182, 365, 730, 1825, 3650, 5475, 7123,
  1300, 1400, 1500, 1600, 1700, 1800, 1900, 2000, 2100, 2200, 2300, 2400, 2500, 2600, 2700, 2800, 2900, 3000,
  3100, 3200, 3300, 3400, 3500, 3600, 3700, 3800, 3900, 4000, 4100, 4200, 4300, 4400, 4500, 4600, 4700, 4800, 4900,
  5000, 5100, 5200, 5300, 5400, 5500, 5600, 5700, 5800, 5900, 6000,
  7300,
];

const REFERENCE_MATURITY_DAYS = new Set<number>([91, 182, 365, 730, 1825, 3650, 5475, 7123]);

function isExcelBlocStart(days: number): boolean {
  return days === 91 || days === 1300 || days === 7300;
}

function roundTo(x: number, n: number): number {
  const p = 10 ** n;
  return Math.round(x * p) / p;
}

function interpoler(points: Point[], x: number): number {
  const pts = [...points].sort((a, b) => a.d - b.d);
  if (x <= pts[0].d) return pts[0].r;
  if (x >= pts[pts.length - 1].d) return pts[pts.length - 1].r;

  for (let i = 0; i < pts.length - 1; i++) {
    const p0 = pts[i];
    const p1 = pts[i + 1];
    if (x >= p0.d && x <= p1.d) {
      const t = (x - p0.d) / (p1.d - p0.d);
      return p0.r + t * (p1.r - p0.r);
    }
  }
  return pts[pts.length - 1].r;
}

function formuleA(k: number, ct: Point[], mlt: Point[]): number {
  if (k > 365) return roundTo(interpoler(mlt, k), 4);
  if (k <= SEUIL_G2) return roundTo(interpoler(ct, k), 4);
  const rMlt = interpoler(mlt, k);
  const v = ((1 + rMlt) ** (k / BASE) - 1) * 360 / k;
  return roundTo(v, 4);
}

/** Aligné backend amortissement : pas d’arrondi 6 dec. sur le secondaire (évite 2,631 % vs 2,632 %). */
function snapSecondaryRaw(raw: number): number {
  return roundTo(raw, 12);
}

function formuleB(k: number, ct: Point[], mlt: Point[]): number {
  // 1 an (365 j) inclus : grille LT (secondaire), aligné backend / Manar — pas l’interp. CT 326→543.
  const raw = k >= 365 ? interpoler(mlt, k) : interpoler(ct, k);
  return snapSecondaryRaw(raw);
}

function zoneOf(k: number): Zone {
  if (k <= 326) return "CT";
  if (k <= 365) return "Transition";
  return "MLT";
}

function fmtPct(x: number, n: number): string {
  return `${(x * 100).toFixed(n).replace(".", ",")}%`;
}

function fmtNum(x: number, n: number): string {
  return x.toFixed(n).replace(".", ",");
}

function fmtYears(days: number): string {
  return `${(Number(days) / 365).toFixed(2).replace(".", ",")} an(s)`;
}

function buildRows(ct: Point[], mlt: Point[]): Row[] {
  return MATURITIES_EXCEL_ORDER.map((k) => {
    const a = formuleA(k, ct, mlt);
    const b = formuleB(k, ct, mlt);
    const diffPct = (a - b) * 100;
    const diffBp = (a - b) * 10000;
    return {
      days: k,
      years: k / 365,
      a,
      b,
      diffPct,
      diffBp,
      zone: zoneOf(k),
      isReferencePilier: REFERENCE_MATURITY_DAYS.has(k),
    };
  });
}

function toCsv(rows: Row[]): string {
  const header = [
    "Zone",
    "Maturité (jours)",
    "Pilier réf. Excel",
    "Années",
    "Formule A",
    "Formule B",
    "Écart (A-B) %",
    "Écart (A-B) bps",
  ];
  const body = rows.map((r) => [
    r.zone,
    String(r.days),
    r.isReferencePilier ? "oui" : "",
    fmtNum(r.years, 1),
    fmtPct(r.a, 4),
    fmtPct(r.b, 6),
    fmtNum(r.diffPct, 6),
    fmtNum(r.diffBp, 3),
  ]);
  return [header, ...body].map((line) => line.join(";")).join("\n");
}

function exportCsv(rows: Row[]) {
  const blob = new Blob([toCsv(rows)], { type: "text/csv;charset=utf-8;" });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = "comparaison_interpolation_bam.csv";
  a.click();
  URL.revokeObjectURL(url);
}

type Props = {
  shortPillars: PillarShort[];
  longPillars: PillarLong[];
};

export default function BamInterpolationComparisonTable({ shortPillars, longPillars }: Props) {
  const rows = useMemo(() => {
    const ct: Point[] = shortPillars
      .map((p) => ({ d: Number(p.maturity_days), r: Number(p.mm_rate_pct) / 100 }))
      .filter((p) => Number.isFinite(p.d) && Number.isFinite(p.r));
    const mlt: Point[] = longPillars
      .map((p) => ({ d: Number(p.maturity_days), r: Number(p.actuarial_rate_pct) / 100 }))
      .filter((p) => Number.isFinite(p.d) && Number.isFinite(p.r));
    if (ct.length === 0 || mlt.length === 0) return [];
    return buildRows(ct, mlt);
  }, [shortPillars, longPillars]);

  let previousZone: Zone | null = null;
  const chartRows = rows.map((r) => ({
    maturity_days: r.days,
    maturity_years: r.years,
    secondary_pct: r.b * 100,
    isReferencePilier: r.isReferencePilier,
  }));
  return (
    <section
      style={{
        background: "var(--wg-card)",
        border: "1px solid var(--wg-border)",
        borderRadius: 10,
        padding: "1rem",
        marginBottom: "1rem",
        overflow: "auto",
        maxHeight: 620,
      }}
    >
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: "0.75rem" }}>
        <h2 style={{ margin: 0, fontSize: "1.1rem", color: "var(--wg-text)" }}>Comparaison interpolation BAM</h2>
        <button
          type="button"
          onClick={() => exportCsv(rows)}
          style={{
            background: "var(--wg-accent)",
            color: "#fff",
            border: "none",
            borderRadius: 8,
            padding: "0.45rem 0.8rem",
            fontWeight: 600,
          }}
        >
          Exporter CSV
        </button>
      </div>
      <p style={{ margin: "0 0 0.75rem", fontSize: "0.78rem", color: "var(--wg-muted)" }}>
        Grille des maturités : <strong>même ordre que la colonne Excel</strong> (1 → 200, piliers référence, puis 1300 à
        6000 par pas de 100, puis 7300 j). Les lignes surlignées reprennent le bloc orange Excel (91, 182, 365, 730,
        1825, 3650, 5475, 7123 j).
      </p>

      <table className="data-table bam-comparison">
        <thead>
          <tr>
            <th>Maturité (jours)</th>
            <th>Taux marché (%)</th>
            <th>Taux secondaire interpolé (%)</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => {
            const zoneChanged = previousZone !== null && previousZone !== r.zone;
            const blocSep = i > 0 && (isExcelBlocStart(r.days) || zoneChanged);
            previousZone = r.zone;
            const significant = Math.abs(r.diffPct) > 0.001;
            return (
              <tr
                key={`${r.days}-${i}`}
                className={`${blocSep ? "bloc-start" : ""} ${r.isReferencePilier ? "row-benchmark" : ""} ${
                  significant ? "diff-significant" : "diff-muted"
                }`.trim()}
              >
                <td>{r.days}</td>
                <td className="mono">{fmtPct(r.a, 4)}</td>
                <td className="mono">{fmtPct(r.b, 6)}</td>
              </tr>
            );
          })}
        </tbody>
      </table>

      {rows.length > 0 && (
        <div style={{ marginTop: "1rem" }}>
          <h3 style={{ margin: "0 0 0.25rem", fontSize: "1rem", color: "var(--wg-text)" }}>
            Courbe des taux secondaire interpolé (%)
          </h3>
          <p style={{ margin: "0 0 0.75rem", fontSize: "0.78rem", color: "var(--wg-muted)" }}>
            Axe X : maturité en jours (même ordre que le tableau Excel ; la courbe relie les points dans cet ordre).
            Axe Y : taux secondaire annuel en pourcentage. Points orange : maturités pilier référence (91–7123 j).
          </p>
          <ResponsiveContainer width="100%" height={300}>
            <LineChart data={chartRows} margin={{ top: 10, right: 24, left: 22, bottom: 28 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#e2e8f0" />
              <XAxis
                dataKey="maturity_days"
                name="Maturité"
                unit=" j"
                tick={{ fontSize: 11 }}
                label={{
                  value: "Maturité (jours)",
                  position: "insideBottom",
                  offset: -18,
                  fontSize: 12,
                  fill: "var(--wg-muted)",
                }}
              />
              <YAxis
                name="Taux secondaire"
                unit="%"
                tick={{ fontSize: 11 }}
                tickFormatter={(v) => `${Number(v).toFixed(3)}%`}
                label={{
                  value: "Taux (%)",
                  angle: -90,
                  position: "insideLeft",
                  offset: -8,
                  fontSize: 12,
                  fill: "var(--wg-muted)",
                }}
              />
              <Tooltip
                formatter={(v: number) => [`${v.toFixed(6)}%`, "Taux secondaire"]}
                labelFormatter={(l) => `Maturité ${l} j (${fmtYears(Number(l))})`}
              />
              <Legend
                verticalAlign="top"
                align="right"
                iconType="line"
                wrapperStyle={{ fontSize: 12, paddingBottom: 8 }}
              />
              <Line
                type="linear"
                dataKey="secondary_pct"
                name="Taux secondaire interpolé (%)"
                stroke="#0d9488"
                strokeWidth={2.2}
                dot={(dotProps: {
                  cx?: number;
                  cy?: number;
                  payload?: { maturity_days?: number; isReferencePilier?: boolean };
                }) => {
                  const { cx, cy, payload } = dotProps;
                  if (cx == null || cy == null || !payload?.isReferencePilier) {
                    return <g />;
                  }
                  return (
                    <circle
                      cx={cx}
                      cy={cy}
                      r={5}
                      fill="#fdba74"
                      stroke="#c2410c"
                      strokeWidth={1.5}
                      aria-label={`Pilier référence ${payload.maturity_days} j`}
                    />
                  );
                }}
                activeDot={{ r: 6 }}
              />
            </LineChart>
          </ResponsiveContainer>
        </div>
      )}
    </section>
  );
}
