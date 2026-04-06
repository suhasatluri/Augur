"use client";

import { useState, useEffect, useCallback } from "react";

interface FeedbackModalProps {
  isOpen: boolean;
  onClose: () => void;
  ticker?: string;
}

const API =
  process.env.NEXT_PUBLIC_API_URL ||
  "https://augur-production-46e9.up.railway.app";

const RATINGS = [
  { value: "positive", label: "\ud83d\udc4d", text: "Useful" },
  { value: "negative", label: "\ud83d\udc4e", text: "Not useful" },
  { value: "neutral", label: "\ud83e\udd14", text: "Unsure" },
] as const;

export default function FeedbackModal({ isOpen, onClose, ticker }: FeedbackModalProps) {
  const [rating, setRating] = useState<string | null>(null);
  const [message, setMessage] = useState("");
  const [email, setEmail] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [submitted, setSubmitted] = useState(false);

  const reset = useCallback(() => {
    setRating(null);
    setMessage("");
    setEmail("");
    setSubmitting(false);
    setSubmitted(false);
  }, []);

  // Close on Escape
  useEffect(() => {
    if (!isOpen) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handler);
    return () => window.removeEventListener("keydown", handler);
  }, [isOpen, onClose]);

  // Reset on open
  useEffect(() => {
    if (isOpen) reset();
  }, [isOpen, reset]);

  // Auto-close after success
  useEffect(() => {
    if (submitted) {
      const t = setTimeout(onClose, 2000);
      return () => clearTimeout(t);
    }
  }, [submitted, onClose]);

  async function handleSubmit() {
    if (!rating) return;
    setSubmitting(true);
    try {
      await fetch(`${API}/feedback`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          rating,
          comment: message || null,
          email: email || null,
          ticker: ticker || null,
          simulation_id: null,
          verdict: null,
          page: "modal",
        }),
      });
    } catch {}
    setSubmitted(true);
    setSubmitting(false);
  }

  if (!isOpen) return null;

  return (
    <div
      className="fixed inset-0 z-50 flex items-center justify-center"
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}
    >
      {/* Overlay */}
      <div className="absolute inset-0 bg-black/70" />

      {/* Modal */}
      <div className="relative w-full max-w-lg mx-4 border border-gold/20 bg-background p-8">
        {/* Close button */}
        <button
          onClick={onClose}
          className="absolute top-4 right-5 text-muted hover:text-gold transition text-lg"
        >
          &times;
        </button>

        {submitted ? (
          <div className="text-center py-8">
            <div className="font-mono text-[11px] text-gold tracking-wide">
              Thank you — feedback received.
            </div>
          </div>
        ) : (
          <>
            {/* Header */}
            <div className="font-mono text-[9px] tracking-[.16em] uppercase text-gold mb-2">
              Help us improve Augur
            </div>
            <h2 className="font-heading text-2xl text-foreground mb-1">
              Share your feedback
            </h2>
            {ticker && (
              <div className="font-mono text-[9px] text-muted tracking-wide mb-6">
                Re: {ticker} simulation
              </div>
            )}
            {!ticker && <div className="mb-6" />}

            {/* Rating */}
            <div className="flex gap-2 mb-5">
              {RATINGS.map(({ value, label, text }) => (
                <button
                  key={value}
                  type="button"
                  onClick={() => setRating(value)}
                  className={`flex items-center gap-1.5 px-4 py-2.5 font-mono text-[11px] border transition cursor-pointer ${
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

            {/* Message */}
            <div className="mb-1">
              <label className="font-mono text-[9px] tracking-[.12em] uppercase text-gold">
                Your thoughts
              </label>
            </div>
            <textarea
              value={message}
              onChange={(e) => setMessage(e.target.value.slice(0, 1000))}
              placeholder="What worked well? What was confusing? What would make this more useful?"
              rows={4}
              className="w-full bg-surface border border-surface-border text-foreground font-mono text-[12px] p-3 resize-none mb-1 outline-none focus:border-gold/50 transition leading-relaxed"
            />
            <div className="font-mono text-[9px] text-muted text-right mb-4">
              {message.length}/1000
            </div>

            {/* Email */}
            <div className="mb-1">
              <label className="font-mono text-[9px] tracking-[.12em] uppercase text-gold">
                Email (optional)
              </label>
            </div>
            <input
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="We'll only use this to follow up"
              className="w-full bg-surface border border-surface-border text-foreground font-mono text-[12px] p-3 mb-5 outline-none focus:border-gold/50 transition"
            />

            {/* Submit */}
            <button
              type="button"
              onClick={handleSubmit}
              disabled={!rating || submitting}
              className="w-full font-mono text-[10px] tracking-widest uppercase py-3 bg-gold text-background border-none cursor-pointer hover:bg-gold-light disabled:opacity-30 disabled:cursor-not-allowed transition"
            >
              {submitting ? "Sending..." : "Send feedback"}
            </button>

            {/* Privacy */}
            <div className="font-mono text-[9px] text-muted text-center mt-4">
              Your feedback is stored securely and used only to improve Augur.
            </div>
          </>
        )}
      </div>
    </div>
  );
}
