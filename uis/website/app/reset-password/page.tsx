import type { Metadata } from "next";
import ResetPasswordPage from "@/components/auth/ResetPasswordPage";

export const metadata: Metadata = {
  title: "Set a new password | TrackFlow",
};

export default function ResetPasswordRoutePage() {
  return <ResetPasswordPage />;
}
