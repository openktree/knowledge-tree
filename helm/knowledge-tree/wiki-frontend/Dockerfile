FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest --activate

# -- Dependencies --
FROM base AS deps
WORKDIR /app
COPY wiki-frontend/package.json wiki-frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# -- Build --
FROM base AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY wiki-frontend/ ./
RUN pnpm build

# -- Production --
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 astro

COPY --from=build --chown=astro:nodejs /app/dist ./dist
COPY --from=build /app/node_modules ./node_modules

USER astro
EXPOSE 4321
ENV HOST="0.0.0.0"
ENV PORT=4321
CMD ["node", "dist/server/entry.mjs"]
