import "next-auth";

// Extend the default Session type to include our JWT accessToken.
// The token is added in lib/auth.ts via the jwt and session callbacks.

declare module "next-auth" {
  interface Session {
    accessToken?: string;
  }
}
