"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { signIn } from "next-auth/react";
import Link from "next/link";
import { useForm } from "react-hook-form";
import { zodResolver } from "@hookform/resolvers/zod";
import { z } from "zod";

const registerSchema = z
  .object({
    name: z.string().min(1, "Full name is required"),
    email: z.string().email("Invalid email address"),
    institution: z.string().min(1, "Institution is required"),
    password: z
      .string()
      .min(8, "Password must be at least 8 characters")
      .regex(/[A-Z]/, "Password must contain at least one uppercase letter")
      .regex(/[0-9]/, "Password must contain at least one number"),
    confirmPassword: z.string(),
  })
  .refine((data) => data.password === data.confirmPassword, {
    message: "Passwords do not match",
    path: ["confirmPassword"],
  });

type RegisterFormData = z.infer<typeof registerSchema>;

export default function RegisterPage() {
  const router = useRouter();
  const [error, setError] = useState<string | null>(null);
  const [isLoading, setIsLoading] = useState(false);

  const {
    register,
    handleSubmit,
    formState: { errors },
  } = useForm<RegisterFormData>({
    resolver: zodResolver(registerSchema),
  });

  const onSubmit = async (data: RegisterFormData) => {
    setIsLoading(true);
    setError(null);

    try {
      const res = await fetch("/api/py/api/auth/register", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          name: data.name,
          email: data.email,
          institution: data.institution,
          password: data.password,
        }),
      });

      if (!res.ok) {
        const errorData = await res.json();
        setError(
          errorData.detail || "Account already exists with this email"
        );
        return;
      }

      // Auto-login after successful registration
      const loginResult = await signIn("credentials", {
        email: data.email,
        password: data.password,
        redirect: false,
      });

      if (!loginResult?.ok) {
        setError("Account created, but login failed. Please try signing in.");
        router.push("/login");
        return;
      }

      router.push("/dashboard");
    } catch (err) {
      setError(
        err instanceof Error ? err.message : "An error occurred during registration"
      );
    } finally {
      setIsLoading(false);
    }
  };

  const inputClass =
    "w-full px-3.5 py-2.5 border border-border rounded-lg focus:outline-none focus:ring-2 focus:ring-primary/40 focus:border-primary text-sm bg-white transition";

  return (
    <div className="space-y-6">
      <div className="space-y-2">
        <div className="lg:hidden flex items-center gap-2 mb-6">
          <div className="w-8 h-8 rounded-lg bg-primary flex items-center justify-center">
            <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
            </svg>
          </div>
          <span className="font-semibold text-foreground">Oxford Cancer Vaccine Design</span>
        </div>
        <h1 className="text-2xl font-bold text-foreground">Create an account</h1>
        <p className="text-sm text-muted-foreground">
          Start predicting personalised neoantigen candidates
        </p>
      </div>

      <div className="rounded-xl border border-border p-6 space-y-4 bg-white shadow-sm">
        <form onSubmit={handleSubmit(onSubmit)} className="space-y-4">
          {error && (
            <div className="p-3 bg-red-50 border border-red-200 rounded-lg text-sm text-red-700">
              {error}
            </div>
          )}

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Full Name</label>
            <input
              type="text"
              placeholder="Dr. Jane Smith"
              {...register("name")}
              className={inputClass}
            />
            {errors.name && (
              <p className="text-xs text-red-600">{errors.name.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Email</label>
            <input
              type="email"
              placeholder="you@institution.edu"
              {...register("email")}
              className={inputClass}
            />
            {errors.email && (
              <p className="text-xs text-red-600">{errors.email.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Institution</label>
            <input
              type="text"
              placeholder="University of Oxford"
              {...register("institution")}
              className={inputClass}
            />
            {errors.institution && (
              <p className="text-xs text-red-600">{errors.institution.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Password</label>
            <input
              type="password"
              placeholder="Min 8 chars, 1 uppercase, 1 number"
              {...register("password")}
              className={inputClass}
            />
            {errors.password && (
              <p className="text-xs text-red-600">{errors.password.message}</p>
            )}
          </div>

          <div className="space-y-1.5">
            <label className="block text-sm font-medium text-foreground">Confirm Password</label>
            <input
              type="password"
              placeholder="Re-enter password"
              {...register("confirmPassword")}
              className={inputClass}
            />
            {errors.confirmPassword && (
              <p className="text-xs text-red-600">{errors.confirmPassword.message}</p>
            )}
          </div>

          <button
            type="submit"
            disabled={isLoading}
            className="w-full px-4 py-2.5 bg-primary text-white rounded-lg hover:bg-primary/90 font-medium transition text-sm disabled:opacity-50 disabled:cursor-not-allowed shadow-sm"
          >
            {isLoading ? "Creating account..." : "Create Account"}
          </button>
        </form>
      </div>

      <p className="text-center text-sm text-muted-foreground">
        Already have an account?{" "}
        <Link href="/login" className="text-primary font-medium hover:underline">
          Sign in
        </Link>
      </p>
    </div>
  );
}
