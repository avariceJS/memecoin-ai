Brain pre-reset Git preservation archive — 2026-10-18

This tag is recovery custody, not an integration baseline. Start development at main.
Parents preserve all 14 commits previously absent from GitHub branch/tag histories.
The blobs/ and trees/ directories retain intermediate/unreachable source objects exactly.
manifest.json maps original object IDs, known paths and local refs.
tag-objects/ stores one negative-test annotated-tag object as raw bytes; recreate only
when explicitly reproducing that negative fixture, using git hash-object -t tag -w.
Historical helpers are inert source snapshots, not approved migration commands.
No work datasets, credentials, runtime databases or Kingston payload were added.
The Control Center topic branch is the resumable UI development surface.
