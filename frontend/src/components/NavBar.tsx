"use client";

import { useState, useEffect } from "react";
import FeedbackModal from "./FeedbackModal";

export default function NavBar() {
  const [feedbackOpen, setFeedbackOpen] = useState(false);
  const [feedbackTicker, setFeedbackTicker] = useState<string | undefined>();

  // Listen for custom event from simulation page
  useEffect(() => {
    const handler = (e: Event) => {
      const detail = (e as CustomEvent).detail || {};
      setFeedbackTicker(detail.ticker || undefined);
      setFeedbackOpen(true);
    };
    window.addEventListener("open-feedback", handler);
    return () => window.removeEventListener("open-feedback", handler);
  }, []);

  return (
    <>
      <header className="border-b border-surface-border px-6 py-4 flex items-center justify-between">
        <a href="/" className="flex items-center gap-3">
          <span className="font-heading text-2xl font-bold text-gold">
            AUGUR
          </span>
          <span className="text-xs text-muted tracking-widest uppercase">
            ASX Earnings Predictor
          </span>
        </a>
        <div className="flex items-center gap-5">
          <a href="/calendar" className="text-xs text-muted tracking-widest uppercase hover:text-gold transition">
            Calendar
          </a>
          <a href="/about" className="text-xs text-muted tracking-widest uppercase hover:text-gold transition">
            How it works
          </a>
          <button
            onClick={() => { setFeedbackTicker(undefined); setFeedbackOpen(true); }}
            className="text-xs text-muted tracking-widest uppercase hover:text-gold transition bg-transparent border-none cursor-pointer"
          >
            Feedback
          </button>
        </div>
      </header>
      <FeedbackModal
        isOpen={feedbackOpen}
        onClose={() => setFeedbackOpen(false)}
        ticker={feedbackTicker}
      />
    </>
  );
}
