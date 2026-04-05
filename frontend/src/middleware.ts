import { NextRequest, NextResponse } from "next/server";
import { getToken } from "next-auth/jwt";

/**
 * Middleware for protecting authenticated routes.
 * Uses NextAuth JWT token to validate session.
 * Protects all routes EXCEPT: /login, /register, /api/auth, /_next, /favicon.ico
 * Redirects unauthenticated users to /login with callbackUrl.
 * Redirects authenticated users away from /login and /register to home.
 */
export async function middleware(request: NextRequest) {
  const pathname = request.nextUrl.pathname;

  // Check for valid JWT token
  const token = await getToken({
    req: request,
    secret: process.env.NEXTAUTH_SECRET,
  });

  // If user is authenticated and trying to access auth pages, redirect to home
  if (token && (pathname === "/login" || pathname === "/register")) {
    return NextResponse.redirect(new URL("/", request.url));
  }

  // Allow unauthenticated access to the landing page
  if (!token && pathname === "/") {
    return NextResponse.next();
  }

  // If user is not authenticated and trying to access protected routes, redirect to login
  if (!token) {
    const loginUrl = new URL("/login", request.url);
    loginUrl.searchParams.set("callbackUrl", pathname);
    return NextResponse.redirect(loginUrl);
  }

  return NextResponse.next();
}

/**
 * Configure which routes the middleware applies to.
 * Protects all routes EXCEPT those explicitly listed.
 */
export const config = {
  matcher: [
    /*
     * Match all request paths except for the ones starting with:
     * - login, register (auth pages)
     * - api/auth (NextAuth endpoints)
     * - _next/static (static files)
     * - _next/image (image optimization files)
     * - favicon.ico (favicon file)
     */
    "/((?!login|register|api/auth|_next/static|_next/image|favicon.ico).*)",
  ],
};
