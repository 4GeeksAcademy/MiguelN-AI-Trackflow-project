import type { Metadata } from "next";
import LoginPage from "@/components/auth/LoginPage";

export const metadata: Metadata = {
  title: "Sign in | TrackFlow",
};

export default function LoginRoutePage() {
  return <LoginPage />;
}
