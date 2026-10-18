# Control Center: read-only Observatory

Integrated into the Brain repository; current operating policy is in ../../docs/operator-runtime.md.
Read AGENTS.md and ../../docs/control-center/ONBOARDING.md. Web v1 is functional; its visuals were
rejected and CC02 reconstruction remains pending. No deployment or new operational backend.

## Verification from repository root

```sh
.venv/bin/python -m unittest discover -s apps/control-center/tests -v
.venv/bin/python apps/control-center/adapter/export_snapshot.py --check
.venv/bin/python apps/control-center/scripts/check_scope.py
.venv/bin/python apps/control-center/scripts/workflow.py --check
npm --prefix apps/control-center/web ci
npm --prefix apps/control-center/web run check
npm --prefix apps/control-center/web test
npm --prefix apps/control-center/web run build
```

The scope guard checks pending edits against HEAD. For a UI-only commit range use --base REF;
never compare the entire integrated core against the old 6a7e079 baseline. Root integration
changes are reviewed separately. Node 24.5 runs the existing tests but some locked dependencies
report a newer engine requirement; retain/disclose that warning, do not silently update the lock.

## Optional local preview

Run `python3 apps/control-center/adapter/server.py` and, separately,
`npm --prefix apps/control-center/web run dev`. Open http://127.0.0.1:5173, not localhost.
Adapter uses 127.0.0.1:8787 and requires X-Control-Center: 1 plus its origin/verb boundary.
Both processes are independent of owner services; stopping them never stops an engine.
No automatic startup, background service or deployment is part of integration.

## Data and acceptance

Published bytes are a pinned historical engineering projection at baseline 6a7e079, not current
Brain/runtime telemetry. Runtime reads only the projection/manifest; offline exporter reads only
nine allowlisted committed evidence/design files. No work/, protected raw payloads or model loads.
Unknown values remain unknown; UI does not grant research admission. Trading, live feeds,
replay and engine controls remain unavailable. servedAt is UI response time, not market freshness.
Do not rewrite pins/projection numbers. Keep recorded engineering metrics separate from new
sparse research results until a reviewed read contract exists. CC02 evidence and visual gates
are preserved; green integration tests do not approve the old visual design.
