# Changelog

## 0.1.0 — 2026-10-22

First public alpha release.

- USB discovery, user-facing app inventory, timestamped layout backups, and editable YAML layouts.
- Folder grouping, ordering, local validation, device diffs, dry runs, push, restore, and post-write verification.
- Preservation of opaque widgets, Smart Stacks, web clips, app bookmarks, empty pages, and explicit folder pagination.
- Deterministic HTML and PNG previews and complete Markdown inventory exports.
- Shared agent instructions and a versioned onboarding guide for Codex, Claude Code, and Gemini CLI.
- Spatial designs for widgets and gaps, with an approved iPhone Mirroring or guided manual workflow that requires no Developer Mode.
- Separate experimental CoreDevice input adapter available only by explicit opt-in.
- Synthetic fixtures, offline tests, package checks, CI, contribution guidance, and public demo assets.

### Known limitations

Hardware coverage is limited to a single development configuration; identifying device details are omitted. Automated widget creation through iPhone Mirroring has not been verified end to end. USB writes can compact spacing and bring App Library-only apps back onto Home Screen pages. Preview icons are illustrative; hidden/Focus status is not inferred. A layout backup does not guarantee recovery of spacing or widget configuration. Shared instructions have not yet been tested in fresh live onboarding sessions with all three agents.
