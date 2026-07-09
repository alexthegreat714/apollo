# Apollo Repository Layout

Apollo is a standalone Git repository with its own `origin` remote. Its parent
`Engineering` repository intentionally excludes `Apollo/` in
`.git/info/exclude` so Git commands in the parent do not absorb Apollo's files.

Run Apollo version-control commands from this directory. Runtime data belongs
under ignored `logs/`, `artifacts/`, `reports/`, and `chroma_db/` paths. Local
credentials belong only in ignored `.env`; `.env.template` is the tracked
configuration reference.
