export const LOCAL_ROLES = ["admin", "designer", "operator", "viewer", "auditor"] as const;
export type LocalRole = typeof LOCAL_ROLES[number];
export const LOCAL_SCOPES = ["read", "run", "design", "credentials", "approve", "review", "admin"] as const;
export type LocalScope = typeof LOCAL_SCOPES[number];
export function scopesForRoles(roles: readonly string[]): LocalScope[] {
  if (roles.includes("admin")) return [...LOCAL_SCOPES];
  return [...new Set<LocalScope>(["read", ...(roles.includes("designer") ? ["run", "design", "credentials"] as LocalScope[] : []),
    ...(roles.includes("operator") ? ["run", "approve"] as LocalScope[] : []),
    ...(roles.some(role => ["designer", "operator", "auditor"].includes(role)) ? ["review"] as LocalScope[] : [])])];
}
export type PrincipalKind = "user" | "service";
export interface LocalPrincipal {
  subject: string;
  username: string;
  kind: PrincipalKind;
  roles: LocalRole[];
  projectIds: string[];
  disabled: boolean;
  createdAt: string;
}
export interface LocalCredential {
  id: string;
  subject: string;
  label: string;
  purpose: "personal" | "api";
  application: string;
  roles: LocalRole[];
  projectIds: string[];
  scopes: LocalScope[];
  expiresAt: string;
  revoked: boolean;
  createdAt: string;
}
export interface CreateLocalPrincipal {
  username: string;
  kind: PrincipalKind;
  roles: LocalRole[];
  projectIds: string[];
}
export interface IssueLocalCredential {
  label: string;
  purpose: "personal" | "api";
  application: string;
  roles: LocalRole[];
  projectIds: string[];
  scopes: LocalScope[];
  expiresAt: string;
}
export interface ChannelBinding {
  id: string;
  provider: string;
  installationId: string;
  externalUserId: string;
  projectId: string;
  subject: string;
}
