"use client";

import { signOut } from "next-auth/react";
import { LogOut } from "lucide-react";

export default function DashboardClient() {
  return (
    <button
      onClick={() => signOut({ redirect: true, callbackUrl: "/login" })}
      className="w-full flex items-center gap-2.5 px-3 py-2 rounded-md hover:bg-white/8 transition text-white/65 hover:text-white/85 text-sm"
    >
      <LogOut className="w-4 h-4" />
      <span className="text-sm font-medium">Sign Out</span>
    </button>
  );
}
