# Jev Trader — web

Next.js (App Router, TypeScript, CSS Modules — no Tailwind) frontend for Jev Trader:
one AI trade decision every Monad block.

## Run

```bash
export BUN_INSTALL_CACHE_DIR="$TMPDIR/bun-cache" BUN_RUNTIME_TRANSPILER_CACHE_PATH=0
bun install
bun run dev      # http://localhost:3001
bun run build
```

Use Bun only — npm is broken on this machine.

## Config

Copy `.env.example` to `.env.local`. `NEXT_PUBLIC_API_URL` points at the backend
(default `http://127.0.0.1:3000`); the app opens an
EventSource on `$NEXT_PUBLIC_API_URL/events`.

## Layout

- `src/lib/types.ts` — wire types (`BlockEvent`, `Decision`, `Fill`, `Meta`, …)
- `src/lib/useFeed.ts` — SSE hook: snapshot / block / fill / ping, 1000-event
  window, 1s→10s reconnect backoff, `connection` state, `avgLatencyMs`
- `src/lib/useUptime.ts` — `useUptime(startedAt)` → ticking `"hh:mm:ss"`
- `src/lib/format.ts` — number/address/tx formatting
- `src/app/globals.css` — design tokens, static status indicators, `.card`
- `src/components/<Name>/<Name>.tsx` — UI components (one folder each)
