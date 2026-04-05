import {
  initializeFaro,
  getWebInstrumentations,
  faro,
} from "@grafana/faro-web-sdk";
import { TracingInstrumentation } from "@grafana/faro-web-tracing";

let initialised = false;

export function initGrafana() {
  if (typeof window === "undefined" || initialised) return;

  const url = process.env.NEXT_PUBLIC_GRAFANA_FARO_URL;
  if (!url) {
    console.warn("Grafana Faro URL not set — skipping");
    return;
  }

  initializeFaro({
    url,
    app: {
      name: process.env.NEXT_PUBLIC_GRAFANA_APP_NAME || "augur-frontend",
      version: "1.2.0",
      environment: process.env.NODE_ENV || "production",
    },
    instrumentations: [
      ...getWebInstrumentations({ captureConsole: true }),
      new TracingInstrumentation(),
    ],
  });

  initialised = true;
}

export function trackSimulationStart(ticker: string, reportingDate: string) {
  if (typeof window === "undefined" || !faro.api) return;
  try {
    faro.api.pushEvent("simulation_started", {
      ticker,
      reporting_date: reportingDate,
    });
  } catch {}
}

export function trackSimulationComplete(
  ticker: string,
  verdict: string,
  durationMs: number
) {
  if (typeof window === "undefined" || !faro.api) return;
  try {
    faro.api.pushEvent("simulation_complete", {
      ticker,
      verdict,
      duration_ms: String(durationMs),
    });
  } catch {}
}

export function trackSimulationError(ticker: string, error: string) {
  if (typeof window === "undefined" || !faro.api) return;
  try {
    faro.api.pushError(new Error(error), {
      type: "simulation_error",
      context: { ticker },
    });
  } catch {}
}
