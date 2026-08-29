FROM node:24-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/coordinator/package.json apps/coordinator/package.json
COPY apps/web/package.json apps/web/package.json
COPY apps/temporal-worker/package.json apps/temporal-worker/package.json
RUN npm ci

COPY apps ./apps
RUN npm run build --workspace @agat/coordinator && npm run build --workspace @agat/web

FROM node:24-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/coordinator/package.json apps/coordinator/package.json
COPY apps/web/package.json apps/web/package.json
COPY apps/temporal-worker/package.json apps/temporal-worker/package.json
RUN npm ci --omit=dev --workspace @agat/coordinator

FROM node:24-bookworm-slim AS runtime

ENV NODE_ENV=production \
    AGAT_HOST=0.0.0.0 \
    AGAT_PORT=8787 \
    AGAT_DB_PATH=/data/agat.db \
    AGAT_ARTIFACTS_DIR=/data/artifacts \
    AGAT_SERVE_WEB=true

WORKDIR /app
RUN mkdir -p /data && chown node:node /data
COPY --from=dependencies --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/apps/coordinator/dist ./apps/coordinator/dist
COPY --from=build --chown=node:node /app/apps/web/dist ./apps/web/dist

USER node
EXPOSE 8787
CMD ["node", "apps/coordinator/dist/server.js"]
