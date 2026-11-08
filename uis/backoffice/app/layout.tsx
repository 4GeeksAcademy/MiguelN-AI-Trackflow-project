import type { Metadata } from "next";
import { IBM_Plex_Mono, Manrope } from "next/font/google";
import "./globals.css";
import { TelemetryTracker } from "@/components/telemetry/TelemetryTracker";

const manrope = Manrope({
  variable: "--font-manrope",
  subsets: ["latin"],
});

const ibmPlexMono = IBM_Plex_Mono({
  variable: "--font-ibm-plex-mono",
  subsets: ["latin"],
  weight: ["400", "500"],
});

export const metadata: Metadata = {
  title: "TrackFlow Backoffice",
  description: "Internal operations workspace for TrackFlow teams, including the supplier directory.",
};

/**
 * Shell only. Navigation lives in `app/(protected)/layout.tsx` because it is
 * session-aware, and the sign-in and registration screens render without it.
 */
export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${manrope.variable} ${ibmPlexMono.variable}`}>
      <body className="min-h-screen text-[color:var(--foreground)]">
        <TelemetryTracker />
        {children}
      </body>
    </html>
  );
}
