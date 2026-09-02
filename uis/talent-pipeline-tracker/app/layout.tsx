import type { Metadata } from "next";
import { Plus_Jakarta_Sans, Space_Grotesk } from "next/font/google";
import { TrackFlowNav } from "@/components/common/TrackFlowNav";
import "./globals.css";

const plusJakartaSans = Plus_Jakarta_Sans({
  variable: "--font-plus-jakarta",
  subsets: ["latin"],
});

const spaceGrotesk = Space_Grotesk({
  variable: "--font-space-grotesk",
  subsets: ["latin"],
});

export const metadata: Metadata = {
  title: "TrackFlow | Talent Pipeline Tracker",
  description: "TrackFlow candidate pipeline management for hiring operations.",
};

export default function RootLayout({
  children,
}: Readonly<{
  children: React.ReactNode;
}>) {
  return (
    <html lang="en" className={`${plusJakartaSans.variable} ${spaceGrotesk.variable} h-full antialiased`}>
      <body className="min-h-full">
        <div className="relative min-h-full overflow-x-hidden bg-[color:var(--background)] text-[color:var(--foreground)]">
          <div className="pointer-events-none absolute inset-0 -z-10 bg-[radial-gradient(1200px_540px_at_92%_-12%,rgba(37,99,235,0.18),transparent_72%),radial-gradient(640px_340px_at_75%_52%,rgba(255,145,66,0.13),transparent_60%),radial-gradient(760px_400px_at_8%_95%,rgba(130,162,229,0.2),transparent_70%),linear-gradient(180deg,#f6faff_0%,#ffffff_46%,#f7faff_100%)]" />
          <TrackFlowNav />
          {children}
        </div>
      </body>
    </html>
  );
}
