# Public Docker cache for disposable CI runners

On 09–10 October 2026 MSK, verification for PRs #178 and #179 repeatedly failed
before Docker integration tests because Docker Hub returned its unauthenticated
pull quota error. Documentation checks, native measurements and browser checks
completed independently; the required aggregate remained failed.

The [composite action](../.github/actions/docker-cache/action.yml) adds
`https://mirror.gcr.io` to the Docker daemon's registry mirrors before the test
and integration jobs start containers. It requires GitHub Actions,
`RUNNER_ENVIRONMENT=github-hosted` and `RUNNER_OS=Linux` before any system mutation.
Existing daemon options and additional mirrors are retained. The candidate JSON
is validated by `dockerd --validate` before installation; after the disposable
runner's daemon restarts, `docker info` must report the cache. Temporary config
files are removed on exit.

The runner uses a new private Docker client config with no credential helpers
for these public pulls. The action pulls the job's first required image before
installing dependencies, so successful configuration alone cannot hide an
unusable registry path. A failed pull remains a failed job and prints the
disposable daemon's recent registry diagnostics.

Image names, version pins and the MinIO build's Golang digest remain unchanged.
Read-only registry probes found BusyBox 1.36, PostgreSQL 17.6-alpine and Temporal
1.8.1 in the cache. The cached manifest for Golang's pinned
`sha256:69a7b9788769bec032d238959b61854e9ae87f57be9029ec04e9885fabf99195`
matched its body SHA and included Linux amd64. The cache is a dependency, so a
missing cached image can still require Docker Hub availability. No job is skipped
or marked successful because image acquisition failed; the existing required
aggregate continues to demand every verification job pass.

[Google's cache documentation](https://docs.cloud.google.com/artifact-registry/docs/pull-cached-dockerhub-images)
describes cached public Docker Hub pulls and fallback to the upstream registry.
[Docker's pull quota documentation](https://docs.docker.com/docker-hub/usage/pulls/)
explains the observed error and shared-address limits.
[Docker daemon validation](https://docs.docker.com/reference/cli/dockerd/#daemon-configuration-file)
checks configuration without starting a daemon. Runtime deployment configuration
and local Docker settings are outside this CI action.
