"use client";

import { useState, useEffect } from "react";
import { useSession } from "next-auth/react";
import Link from "next/link";
import { usePathname } from "next/navigation";
import { Lock } from "lucide-react";

/**
 * Client component that checks admin status from the backend (not just JWT)
 * so that newly promoted admins can see the admin link without re-login.
 */
export default function AdminNavLink() {
  const { data: session } = useSession();
  const pathname = usePathname();
  const [isAdmin, setIsAdmin] = useState<boolean>(false);

  useEffect(() => {
    if (!session?.accessToken) return;
    // Check fresh admin status from backend
    fetch("/api/py/api/auth/me", {
      headers: { Authorization: `Bearer ${session.accessToken}` },
    })
      .then((r) => r.json())
      .then((u) => setIsAdmin(u.is_admin === true))
      .catch(() => setIsAdmin(false));
  }, [session?.accessToken]);

  // Also use JWT value as initial state (avoids flash)
  useEffect(() => {
    if (session?.user?.is_admin) setIsAdmin(true);
  }, [session?.user?.is_admin]);

  if (!isAdmin) return null;

  const isActive = pathname === "/admin";

  return (
    <div className="pt-4 mt-4 border-t border-white/10 space-y-1">
      <p className="text-[10px] font-semibold text-white/40 uppercase tracking-wider mb-2 px-3">
        Admin
      </p>
      <Link
        href="/admin"
        className={`flex items-center gap-2.5 px-3 py-2 rounded-md text-sm transition ${
          isActive
            ? "bg-white/15 text-white font-medium"
            : "text-white/70 hover:text-white hover:bg-white/10"
        }`}
      >
        <Lock className="w-4 h-4" />
        Admin Panel
      </Link>
    </div>
  );
}
