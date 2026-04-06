const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000";
const API_KEY = process.env.NEXT_PUBLIC_API_KEY || "augur-dev-key";

const headers = {
  "Content-Type": "application/json",
  "X-API-Key": API_KEY,
};

export interface SimulateResponse {
  job_id: string;
  simulation_id: string;
  status: string;
  estimated_minutes: number;
  disclaimer: string;
}

export interface SwingFactor {
  theme: string;
  description: string;
  bull_view: string;
  bear_view: string;
  mentions: number;
  disagreement_score: number;
}

export interface PredictionResult {
  simulation_id: string;
  ticker: string;
  verdict: string;
  distribution: {
    p_beat: number;
    p_miss: number;
    p_inline: number;
    mean_probability: number;
    median_probability: number;
    std_dev: number;
    confidence_band_low: number;
    confidence_band_high: number;
  };
  swing_factors: SwingFactor[];
  sentiment_cascade: {
    direction: string;
    severity: string;
    retail_conviction: number;
    retail_mean_probability: number;
    reasoning: string;
  };
  convergence_score: number;
  high_uncertainty: boolean;
  human_summary: string;
  disclaimer: string;
}

export interface SimulationStatus {
  job_id: string;
  simulation_id: string;
  ticker: string;
  status: "queued" | "running" | "complete" | "failed";
  reporting_date: string | null;
  result: PredictionResult | null;
  error: string | null;
  disclaimer: string;
}

export interface SimulationListItem {
  simulation_id: string;
  ticker: string;
  status: string;
  verdict: string | null;
  created_at: string | null;
  disclaimer: string;
}

export async function startSimulation(
  ticker: string,
  reportingDate: string
): Promise<SimulateResponse> {
  const res = await fetch(`${API_URL}/simulate`, {
    method: "POST",
    headers,
    body: JSON.stringify({ ticker, reporting_date: reportingDate }),
  });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function getSimulationStatus(
  jobId: string
): Promise<SimulationStatus> {
  const res = await fetch(`${API_URL}/simulation/${jobId}`, { headers });
  if (!res.ok) {
    const err = await res.json();
    throw new Error(err.detail || `HTTP ${res.status}`);
  }
  return res.json();
}

export async function listSimulations(): Promise<SimulationListItem[]> {
  const res = await fetch(`${API_URL}/simulations`, { headers });
  if (!res.ok) return [];
  return res.json();
}

export interface ActivityItem {
  ticker: string;
  count: number;
  last_verdict: string | null;
}

export async function getActivity(
  period: "today" | "week" = "today"
): Promise<ActivityItem[]> {
  const res = await fetch(`${API_URL}/activity?period=${period}`);
  if (!res.ok) return [];
  return res.json();
}

export interface CalendarEntry {
  ticker: string;
  company: string;
  report_type: string | null;
  sector: string | null;
  source: string | null;
  confidence: string;
}

export interface CalendarData {
  calendar: Record<string, CalendarEntry[]>;
  sectors: string[];
  total_companies: number;
  last_updated: string | null;
  disclaimer: string;
}

export async function getCalendar(params?: {
  weeks?: number;
  sector?: string | null;
  show_past?: boolean;
  search?: string;
}): Promise<CalendarData> {
  const qs = new URLSearchParams();
  if (params?.weeks) qs.set("weeks", String(params.weeks));
  if (params?.sector) qs.set("sector", params.sector);
  if (params?.show_past) qs.set("show_past", "true");
  if (params?.search) qs.set("search", params.search);
  const res = await fetch(`${API_URL}/calendar?${qs}`);
  if (!res.ok) return { calendar: {}, sectors: [], total_companies: 0, last_updated: null, disclaimer: "" };
  return res.json();
}
