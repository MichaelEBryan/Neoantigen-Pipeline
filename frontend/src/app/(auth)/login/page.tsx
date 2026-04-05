"use client";

import { Suspense, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const loginSchema = z.object({
  email: z.string().email("Invalid email address"),
  password: z.string().min(8, "Password must be at least 8 characters"),
});

type LoginFormData = z.infer<typeof loginSchema>;

function LoginForm() {
  const router = useRouter();
  const searchParams = useSearchParams();
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<LoginFormData>({
    resolver: zodResolver(loginSchema),
  });

  const onSubmit = async (data: LoginFormData) => {
    setIsLoading(true);
    setError(null);

    try {
      const result = await signIn("credentials", {
        email: data.email,
        password: data.password,
        redirect: false,
      });

      if (!result?.ok) {
        setError(result?.error || "Invalid email or password");
        return;
      }

      const raw = searchParams.get("callbackUrl") || "/dashboard";
      const callbackUrl = raw.startsWith("/") && !raw.startsWith("//") ? raw : "/dashboard";
      router.push(callbackUrl);
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "An error occurred during login"
      );
    } finally {
      setIsLoading(false);
    }
  };

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        {/* Show branding on mobile only (hidden on desktop where left panel shows it) */}
        <div className="lg:hidden flex items-center gap-2 mb-6">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
            </svg>
          </div>
          <span className="font-semibold text-foreground">Oxford Cancer Vaccine Design</span>
        </div>
        <h1 className="text-2xl font-bold text-foreground">Welcome back</h1>
        <p className="text-sm text-muted-foreground">
          Sign in to access your analyses and projects
        </p>
      </div>

      <div className="rounded-xl border border-border p-6 space-y-5 bg-white shadow-sm">
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Email</label>
            <input
              type="email"
              placeholder="you@institution.edu"
              {...register("email")}
              className="w-full px-3.5 py-2.5 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary text-sm bg-white transition"
            />
            {errors.email && (
              <p className="text-xs text-red-600">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Password</label>
            <input
              type="password"
              placeholder="Enter your password"
              {...register("password")}
              className="w-full px-3.5 py-2.5 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary text-sm bg-white transition"
            />
            {errors.password && (
              <p className="text-xs text-red-600">{errors.password.message}</p>
            )}
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full px-4 py-2.5 bg-primary text-white rounded-lg hover:bg-primary/90 font-medium transition text-sm disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {isLoading ? "Signing in..." : "Sign In"}
          </button>
        </form>
      </div>

      <p className="text-center text-sm text-muted-foreground">
        Don&apos;t have an account?{" "}
        <Link href="/register" className="text-primary font-medium hover:underline">
          Create one
        </Link>
      </p>
    </div>
  );
}

export default function LoginPage() {
  return (
    <Suspense fallback={<div className="text-center p-8 text-muted-foreground">Loading...</div>}>
      <LoginForm />
    </Suspense>
  );
}
