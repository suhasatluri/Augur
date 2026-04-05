"use client";

import { useState } from "react";

interface FeedbackFormProps {
  simulationId?: string;
  ticker?: string;
  verdict?: string;
  page?: string;
}

const API =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://augur-production-46e9.up.railway.app";

export default function FeedbackForm({
  simulationId,
  ticker,
  verdict,
  page = "results",
}: FeedbackFormProps) {
  const [rating, setRating] = useState<string | null>(null);
  const [comment, setComment] = useState("");
  const [email, setEmail] = useState("");
  const [submitted, setSubmitted] = useState(false);
  const [submitting, setSubmitting] = useState(false);

  async function handleSubmit() {
    if (!rating) return;
    setSubmitting(true);
    try {
      await fetch(`${API}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating,
          comment: comment || null,
          email: email || null,
          simulation_id: simulationId || null,
          ticker: ticker || null,
          verdict: verdict || null,
          page,
        }),
      });
    } catch {}
    setSubmitted(true);
    setSubmitting(false);
  }

  if (submitted) {
    return (
      <div className="border border-gold/20 bg-gold/[.04] p-4 text-center font-mono text-[11px] text-gold tracking-wide">
        Thank you — feedback received.
      </div>
    );
  }

  return (
    <div className="border border-gold/20 bg-gold/[.04] p-5">
      <div className="font-mono text-[9px] tracking-[.16em] uppercase text-gold mb-3.5">
        {page === "results" ? "Was this simulation useful?" : "Share your thoughts"}
      </div>

      {/* Rating buttons */}
      <div className="flex gap-2 mb-4">
        {[
          { value: "positive", label: "\ud83d\udc4d", text: "Yes" },
          { value: "negative", label: "\ud83d\udc4e", text: "No" },
          { value: "neutral", label: "\ud83e\udd14", text: "Not sure" },
        ].map(({ value, label, text }) => (
          <button
            key={value}
            type="button"
            onClick={() => setRating(value)}
            className={`flex items-center gap-1.5 px-3.5 py-2 font-mono text-[11px] border transition cursor-pointer ${
              rating === value
                ? "border-gold bg-gold/[.12] text-gold"
                : "border-gold/20 text-muted hover:border-gold/40"
            }`}
          >
            <span>{label}</span>
            <span>{text}</span>
          </button>
        ))}
      </div>

      {/* Comment + email — show after rating */}
      {rating && (
        <>
          <textarea
            placeholder="What could be better? (optional)"
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            rows={2}
            className="w-full bg-surface border border-surface-border text-foreground font-mono text-[11px] p-2.5 resize-none mb-2 outline-none focus:border-gold/50 transition"
          />
          <input
            type="email"
            placeholder="Email (optional — for follow-up)"
            value={email}
            onChange={(e) => setEmail(e.target.value)}
            className="w-full bg-surface border border-surface-border text-foreground font-mono text-[11px] p-2.5 mb-3 outline-none focus:border-gold/50 transition"
          />
          <button
            type="button"
            onClick={handleSubmit}
            disabled={submitting}
            className="font-mono text-[10px] tracking-widest uppercase px-6 py-2.5 bg-gold text-background border-none cursor-pointer hover:bg-gold-light disabled:opacity-50 disabled:cursor-not-allowed transition"
          >
            {submitting ? "Submitting..." : "Submit feedback"}
          </button>
        </>
      )}
    </div>
  );
}
