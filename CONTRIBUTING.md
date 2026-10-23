# Contributing

Thanks for helping improve iOS Organizer. Start with [AGENTS.md](AGENTS.md) and the [CLI reference](docs/cli-reference.md). Keep a change focused and describe the user-visible result, validation, and remaining limits.

## Local setup

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
.venv/bin/pytest -q
python3 scripts/check_release.py
```

The offline tests require no phone. Keep device access in `device.py`; compiler, validation, organization, and preview code must run without a live phone. Add behavioral tests when changing parsing, validation, preservation, or device failure handling. Do not run hardware writes as part of ordinary development or CI.

Use generated fictional fixtures. Keep phone backups, installed app collections, device IDs, pairing files, screenshots, and inventories in ignored runtime directories. Check a proposed diff before publishing it. Redact logs before attaching them to an issue.

## Agent onboarding changes

Maintain the shared entry point in `AGENTS.md`; Claude and Gemini import it. Keep the versioned conversation guide in `onboarding.py` and the detailed procedure in `docs/agent-workflow.md` consistent. Preserve inventory before questions, image review before applying, and honest verification. Instructions guide agents; they do not guarantee that every model follows them.

## Compatibility reports

State macOS/iOS versions, Python version, command, expected result, and observed result. Indicate whether it was an offline test, a read-only phone check, or an explicitly authorized write. A successful USB read does not prove exact spacing or widget automation works.

## Release checks

1. Run the test matrix and `scripts/check_release.py` on the staged/tracked files.
2. Regenerate public previews with `.venv/bin/python scripts/generate_demo.py`; inspect them and verify every public artifact is fictional.
3. Build with `python -m build`, check metadata with `python -m twine check dist/*`, and inspect the wheel/source file lists for private data.
4. Update `CHANGELOG.md` and package version together. Verify repository links, agent entry points, example commands, and CI status.
5. Publish only reviewed source. Real-device testing and cross-agent onboarding trials must be described accurately, with remaining limitations in the release notes.

Contributions are provided under the project's GPL-3.0-or-later license. Do not introduce third-party source without its license and attribution.
