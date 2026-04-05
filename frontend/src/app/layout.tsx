import type { Metadata } from "next";
import { Cormorant_Garamond, JetBrains_Mono } from "next/font/google";
import "./globals.css";

const cormorant = Cormorant_Garamond({
  subsets: ["latin"],
  weight: ["400", "600", "700"],
  variable: "--font-cormorant",
});

const jetbrains = JetBrains_Mono({
  subsets: ["latin"],
  weight: ["400", "500"],
  variable: "--font-jetbrains",
});

export const metadata: Metadata = {
  title: "Augur — ASX Earnings Predictor",
  description: "Swarm intelligence platform for ASX earnings surprise prediction",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en">
      <body
        className={`${cormorant.variable} ${jetbrains.variable} font-mono antialiased bg-background text-foreground`}
      >
        <div className="min-h-screen flex flex-col">
          <header className="border-b border-surface-border px-6 py-4 flex items-center justify-between">
            <a href="/" className="flex items-center gap-3">
              <span className="font-heading text-2xl font-bold text-gold">
                AUGUR
              </span>
              <span className="text-xs text-muted tracking-widest uppercase">
                ASX Earnings Predictor
              </span>
            </a>
            <a href="/about" className="text-xs text-muted tracking-widest uppercase hover:text-gold transition">
              How it works
            </a>
          </header>
          <main className="flex-1 px-6 py-8 max-w-5xl mx-auto w-full">
            {children}
          </main>
          <footer className="border-t border-surface-border px-6 py-4 text-center text-xs text-muted">
            This is not financial advice. Research tool only. Augur does not hold an AFSL.
          </footer>
        </div>
      </body>
    </html>
  );
}
