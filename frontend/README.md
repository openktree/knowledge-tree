# Knowledge Tree — Frontend

Next.js 15 (App Router) frontend with Cytoscape.js graph visualization, shadcn/ui components, and Tailwind CSS.

## Prerequisites

- **Node.js 20+**
- **[pnpm](https://pnpm.io/)** — Package manager (required; do NOT use npm or yarn)

## Setup

```bash
cd frontend
pnpm install
```

## Running

```bash
# Development server (http://localhost:3000)
cd frontend && pnpm dev

# Production build
cd frontend && pnpm build
cd frontend && pnpm start
```

## Testing

```bash
# Run all tests
cd frontend && pnpm test

# Watch mode
cd frontend && pnpm test:watch

# Type checking
cd frontend && pnpm type-check

# Linting
cd frontend && pnpm lint
```

## Full Verification

Run all three checks before considering any change complete:

```bash
cd frontend && pnpm lint && pnpm type-check && pnpm test
```

## Adding Dependencies

Always use `pnpm` to add dependencies. Never edit `package.json` directly.

```bash
# Runtime dependency
cd frontend && pnpm add <package>

# Dev dependency
cd frontend && pnpm add -D <package>
```

## Adding UI Components (shadcn)

```bash
cd frontend && pnpm dlx shadcn@latest add <component>
```
