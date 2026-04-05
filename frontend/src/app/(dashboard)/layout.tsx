import Link from "next/link";
import { Home, FileText, FolderOpen, Clock, Shield, Settings, BarChart3, UserCog } from "lucide-react";
import { getServerSession } from "next-auth";
import { authOptions } from "@/lib/auth";
import DashboardClient from "@/components/dashboard-client";
import TermsModal from "@/components/terms-modal";
import AdminNavLink from "@/components/admin-nav-link";

export default async function DashboardLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  const session = await getServerSession(authOptions);

  if (!session?.user) {
    return null;
  }

  return (
    <div className="flex h-screen bg-background">
      {/* Sidebar -- dark Oxford navy */}
      <aside className="w-60 bg-sidebar text-sidebar-foreground flex flex-col shrink-0">
        <div className="p-5 flex-1 overflow-auto">
          {/* Logo */}
          <div className="flex items-center gap-2.5 mb-8">
            <div className="w-8 h-8 rounded-lg bg-white/15 flex items-center justify-center shrink-0">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="white" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
                <path d="M12 2v6m0 8v6m-6-10H2m20 0h-4M7.8 7.8 4.6 4.6m14.8 14.8-3.2-3.2M7.8 16.2l-3.2 3.2M19.4 4.6l-3.2 3.2"/>
              </svg>
            </div>
            <div>
              <p className="text-sm font-bold text-white leading-tight">OCVD</p>
              <p className="text-[10px] text-white/60 leading-tight">Cancer Vaccine Design</p>
            </div>
          </div>

          {/* Navigation */}
          <nav className="space-y-1">
            <p className="text-[10px] font-semibold text-white/40 uppercase tracking-wider mb-2 px-3">
              Main
            </p>
            <NavLink href="/dashboard" label="Dashboard" icon={<Home className="w-4 h-4" />} />
            <NavLink href="/analysis/new" label="New Analysis" icon={<FileText className="w-4 h-4" />} />
            <NavLink href="/projects" label="Projects" icon={<FolderOpen className="w-4 h-4" />} />
            <NavLink href="/history" label="History" icon={<Clock className="w-4 h-4" />} />
            <NavLink href="/compare" label="Compare" icon={<BarChart3 className="w-4 h-4" />} />

            {/* Admin link checks backend for fresh is_admin status */}
            <AdminNavLink />

            <div className="pt-4 mt-4 border-t border-white/10 space-y-1">
              <p className="text-[10px] font-semibold text-white/40 uppercase tracking-wider mb-2 px-3">
                Settings
              </p>
              <NavLink href="/settings" label="Settings" icon={<UserCog className="w-4 h-4" />} />
              <NavLink href="/settings/data" label="Data & Privacy" icon={<Shield className="w-4 h-4" />} />
              <NavLink href="/terms" label="Terms" icon={<Settings className="w-4 h-4" />} />
            </div>
          </nav>
        </div>

        {/* Footer with user info and logout */}
        <div className="border-t border-white/10 p-4">
          <div className="space-y-3">
            <div className="flex items-center gap-2.5">
              <div className="w-8 h-8 rounded-full bg-white/15 text-white flex items-center justify-center text-xs font-bold shrink-0">
                {session.user.name?.charAt(0)?.toUpperCase() || "?"}
              </div>
              <div className="min-w-0">
                <p className="text-xs font-medium text-white truncate">{session.user.name}</p>
                <p className="text-[10px] text-white/50 truncate">{session.user.email}</p>
              </div>
            </div>
            <DashboardClient />
          </div>
        </div>
      </aside>

      {/* Main Content */}
      <main className="flex-1 overflow-auto">
        <div className="max-w-5xl mx-auto p-8">{children}</div>
      </main>

      {/* Terms acceptance modal */}
      <TermsModal />
    </div>
  );
}

function NavLink({
  href,
  label,
  icon,
}: {
  href: string;
  label: string;
  icon: React.ReactNode;
}) {
  return (
    <Link
      href={href}
      className="flex items-center gap-2.5 px-3 py-2 rounded-md hover:bg-white/8 transition text-white/65 hover:text-white/85 text-sm"
    >
      {icon}
      <span className="font-medium">{label}</span>
    </Link>
  );
}
