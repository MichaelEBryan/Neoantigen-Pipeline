import type { NextAuthOptions, DefaultSession } from "next-auth";
import CredentialsProvider from "next-auth/providers/credentials";

declare module "next-auth" {
  interface Session extends DefaultSession {
    user?: {
      id: number;
      email: string;
      name: string;
      institution: string;
      is_admin: boolean;
      terms_accepted_at: string | null;
    };
    accessToken?: string;
  }
}

declare module "next-auth/jwt" {
  interface JWT {
    accessToken?: string;
    user?: {
      id: number;
      email: string;
      name: string;
      institution: string;
      is_admin: boolean;
      terms_accepted_at: string | null;
    };
  }
}

export const authOptions: NextAuthOptions = {
  providers: [
    CredentialsProvider({
      name: "Credentials",
      credentials: {
        email: { label: "Email", type: "email" },
        password: { label: "Password", type: "password" },
      },
      async authorize(credentials) {
        if (!credentials?.email || !credentials?.password) {
          throw new Error("Invalid credentials");
        }

        try {
          const res = await fetch(
            `${process.env.BACKEND_INTERNAL_URL || "http://localhost:8000"}/api/auth/login`,
            {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify({
                email: credentials.email,
                password: credentials.password,
              }),
            }
          );

          if (!res.ok) {
            const error = await res.json();
            throw new Error(error.detail || "Invalid credentials");
          }

          const data = await res.json();

          return {
            id: data.user.id,
            email: data.user.email,
            name: data.user.name,
            institution: data.user.institution,
            is_admin: data.user.is_admin ?? false,
            terms_accepted_at: data.user.terms_accepted_at,
            accessToken: data.access_token,
          };
        } catch (error) {
          throw new Error(
            error instanceof Error ? error.message : "Authentication failed"
          );
        }
      },
    }),
  ],
  session: {
    strategy: "jwt",
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  jwt: {
    maxAge: 30 * 24 * 60 * 60, // 30 days
  },
  callbacks: {
    async jwt({ token, user, trigger }) {
      // On initial sign in, store user data + access token
      if (user) {
        token.accessToken = (user as any).accessToken;
        token.user = {
          id: (user as any).id,
          email: (user as any).email,
          name: (user as any).name,
          institution: (user as any).institution,
          is_admin: (user as any).is_admin ?? false,
          terms_accepted_at: (user as any).terms_accepted_at,
        };
      }

      // On session update (e.g. after accepting terms), re-fetch user from backend
      if (trigger === "update" && token.accessToken) {
        try {
          const res = await fetch(
            `${process.env.BACKEND_INTERNAL_URL || "http://localhost:8000"}/api/auth/me`,
            {
              headers: { Authorization: `Bearer ${token.accessToken}` },
            }
          );
          if (res.ok) {
            const userData = await res.json();
            token.user = {
              id: userData.id,
              email: userData.email,
              name: userData.name,
              institution: userData.institution,
              is_admin: userData.is_admin ?? false,
              terms_accepted_at: userData.terms_accepted_at,
            };
          }
        } catch {
          // Silently fail -- keep existing token data
        }
      }

      return token;
    },
    async session({ session, token }) {
      if (token.user) {
        session.user = token.user;
      }
      if (token.accessToken) {
        session.accessToken = token.accessToken;
      }
      return session;
    },
  },
  pages: {
    signIn: "/login",
  },
};
