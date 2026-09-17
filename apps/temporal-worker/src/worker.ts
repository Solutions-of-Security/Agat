import { NativeConnection, Runtime, Worker } from "@temporalio/worker";
import { fileURLToPath } from "node:url";

import * as activities from "./activities.js";
import {
  loadTemporalWorkerConfig,
  temporalWorkerConnectionOptions,
  temporalWorkerDeploymentOptions,
} from "./config.js";

const config = loadTemporalWorkerConfig();

Runtime.install({
  telemetryOptions: {
    metrics: {
      prometheus: { bindAddress: config.metricsAddress },
    },
  },
});

const connection = await NativeConnection.connect(temporalWorkerConnectionOptions(config));
const worker = await Worker.create({
  connection,
  namespace: config.namespace,
  taskQueue: config.taskQueue,
  identity: config.identity,
  workflowBundle: {
    codePath: fileURLToPath(new URL("./workflow-bundle.js", import.meta.url)),
  },
  activities,
  maxConcurrentWorkflowTaskExecutions: config.maxConcurrentWorkflowTasks,
  maxConcurrentActivityTaskExecutions: config.maxConcurrentActivityTasks,
  shutdownGraceTime: `${config.shutdownGraceSeconds} seconds`,
  workerDeploymentOptions: temporalWorkerDeploymentOptions(config),
});

console.log(
  `Temporal worker слушает ${config.taskQueue} в namespace ${config.namespace} · `
  + `target=${config.target} · ring=${config.deploymentRing} · `
  + `version=${config.versioningEnabled ? `${config.deploymentName}.${config.buildId}` : "unversioned"}`,
);
await worker.run();
