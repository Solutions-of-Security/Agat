import { AsyncLocalStorage } from "node:async_hooks";
import type { AuthContext } from "./auth.js";

const identityScope = new AsyncLocalStorage<AuthContext | null>();
export const withLocalIdentityScope = <T>(callback: () => T): T => identityScope.run(null, callback);
export const enterLocalIdentity = (context: AuthContext): void => identityScope.enterWith(context);
export const currentLocalIdentity = (): AuthContext | null => identityScope.getStore() ?? null;
export function identityAudit(context: AuthContext | null): Record<string, unknown> | undefined {
  return context?.credentialId ? { subject: context.subject, kind: context.principalKind, source: context.identitySource,
    application: context.application, credentialId: context.credentialId } : undefined;
}
