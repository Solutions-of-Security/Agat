FROM node:24-bookworm-slim AS build

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/coordinator/package.json apps/coordinator/package.json
COPY apps/web/package.json apps/web/package.json
COPY apps/temporal-worker/package.json apps/temporal-worker/package.json
RUN npm ci

COPY apps/temporal-worker apps/temporal-worker
RUN npm run build --workspace @agat/temporal-worker

FROM node:24-bookworm-slim AS dependencies

WORKDIR /app
COPY package.json package-lock.json ./
COPY apps/coordinator/package.json apps/coordinator/package.json
COPY apps/web/package.json apps/web/package.json
COPY apps/temporal-worker/package.json apps/temporal-worker/package.json
RUN npm ci --omit=dev --workspace @agat/temporal-worker

FROM node:24-bookworm-slim AS runtime

ENV NODE_ENV=production
WORKDIR /app
COPY --from=dependencies --chown=node:node /app/node_modules ./node_modules
COPY --from=build --chown=node:node /app/apps/temporal-worker/dist ./apps/temporal-worker/dist

USER node
EXPOSE 9091
CMD ["node", "apps/temporal-worker/dist/worker.js"]
