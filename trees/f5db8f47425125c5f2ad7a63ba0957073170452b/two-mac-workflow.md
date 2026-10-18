# Compatibility Pointer: Two-Host Workflow

The current Mac 1/Mac 2 ownership, artifact transfer, quota, and release rules are in
[`operator-runtime.md`](operator-runtime.md). Worker bootstrap is `tools/bootstrap_worker.command`.

This path remains because the bootstrap command prints its name. It contains no current host
status, credentials, owner runtime, or acquisition authorization.
