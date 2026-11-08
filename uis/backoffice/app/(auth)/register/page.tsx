import type { Metadata } from "next";
import RegisterPage from "@/components/auth/RegisterPage";

export const metadata: Metadata = {
  title: "Create account | TrackFlow Backoffice",
};

export default function RegisterRoutePage() {
  return <RegisterPage />;
}
