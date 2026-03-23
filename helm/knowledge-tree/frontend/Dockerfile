FROM node:22-alpine AS base
RUN corepack enable && corepack prepare pnpm@latest --activate

# -- Dependencies --
FROM base AS deps
WORKDIR /app
COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN pnpm install --frozen-lockfile

# -- Build --
FROM base AS build
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY frontend/ ./
# Build with placeholder — replaced at runtime via entrypoint
ARG NEXT_PUBLIC_API_URL="__NEXT_PUBLIC_API_URL_PLACEHOLDER__"
ENV NEXT_PUBLIC_API_URL=$NEXT_PUBLIC_API_URL
RUN pnpm build

# -- Production --
FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
RUN addgroup --system --gid 1001 nodejs && adduser --system --uid 1001 nextjs
RUN apk add --no-cache su-exec

COPY --from=build /app/public ./public
COPY --from=build --chown=nextjs:nodejs /app/.next/standalone ./
COPY --from=build --chown=nextjs:nodejs /app/.next/static ./.next/static

# Entrypoint: replace API URL placeholder as root, then drop to nextjs
COPY <<'SCRIPT' /app/entrypoint.sh
#!/bin/sh
set -e
if [ -n "$NEXT_PUBLIC_API_URL" ] && [ "$NEXT_PUBLIC_API_URL" != "__NEXT_PUBLIC_API_URL_PLACEHOLDER__" ]; then
  echo "Replacing API URL placeholder with: $NEXT_PUBLIC_API_URL"
  find /app/.next -name '*.js' -exec sed -i "s|__NEXT_PUBLIC_API_URL_PLACEHOLDER__|$NEXT_PUBLIC_API_URL|g" {} +
  find /app -maxdepth 1 -name '*.js' -exec sed -i "s|__NEXT_PUBLIC_API_URL_PLACEHOLDER__|$NEXT_PUBLIC_API_URL|g" {} +
fi
exec su-exec nextjs node server.js
SCRIPT
RUN chmod +x /app/entrypoint.sh

EXPOSE 3000
ENV PORT=3000
ENV HOSTNAME="0.0.0.0"
CMD ["/app/entrypoint.sh"]
