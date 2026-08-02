"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { useAuth } from "@/lib/auth";
import AuthForm from "@/components/auth/AuthForm";

export default function LoginPage() {
  const { user, loading } = useAuth();
  const router = useRouter();

  // Already signed in → skip the form.
  useEffect(() => {
    if (!loading && user) router.replace("/");
  }, [loading, user, router]);

  return <AuthForm mode="login" />;
}
