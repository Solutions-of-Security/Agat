export const DEPLOYMENT_CONTRACT_VERSION = 1;
export type DeploymentProfile = "single-user" | "lite" | "durable" | "production-ha";
export type AuthenticationMode = "legacy" | "oidc" | "local";
export type DeploymentOperation = "runs" | "processes" | "schedules" | "multiuser" | "mcpHttp" | "wasi" | "container" | "workspace" | "stdio" | "chat" | "callbacks" | "cloudModels" | "productionHa";

export interface DeploymentCapability {
  operation: DeploymentOperation;
  label: string;
  available: boolean;
  code: string;
  message: string;
}

export interface DeploymentSnapshot {
  contractVersion: number;
  profile: DeploymentProfile;
  label: string;
  ready: boolean;
  controlPlaneAvailable: boolean;
  authentication: { mode: AuthenticationMode; individualSubjects: boolean; label: string };
  stateStore: "sqlite" | "postgresql";
  runtime: "database" | "temporal";
  ingress: "direct" | "gateway";
  productionQualified: false;
  blockers: Array<{ code: string; message: string }>;
  capabilities: DeploymentCapability[];
}
