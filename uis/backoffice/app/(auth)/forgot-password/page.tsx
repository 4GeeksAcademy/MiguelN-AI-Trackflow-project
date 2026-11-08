import type { Metadata } from "next";
import ForgotPasswordPage from "@/components/auth/ForgotPasswordPage";

export const metadata: Metadata = {
  title: "Forgot password | TrackFlow Backoffice",
};

export default function ForgotPasswordRoutePage() {
  return <ForgotPasswordPage />;
}
