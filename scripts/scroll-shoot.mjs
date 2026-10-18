#!/usr/bin/env node
// Compatibility entry point; implementation URL and imports stay component-local.
await import(new URL('../apps/research/scroll-shoot.mjs', import.meta.url));
