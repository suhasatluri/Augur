"use client";

import { useEffect } from "react";
import { initGrafana } from "@/lib/grafana";

export function GrafanaInit() {
  useEffect(() => {
    initGrafana();
  }, []);
  return null;
}
