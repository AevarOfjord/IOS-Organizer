# Security and privacy

This project has local USB access to a trusted iPhone. Layout writes can change its Home Screen. Use a reviewed proposal, a fresh backup, and readback verification. Protect your Mac's pairing records and handle trust prompts and passcodes yourself.

The current 0.1.x release is experimental. Security fixes target the latest release; older versions have no maintenance commitment.

## Report a vulnerability

Use [GitHub's private vulnerability reporting](https://github.com/AevarOfjord/IOS-Organizer/security/advisories/new) when available. If that option is unavailable, open an issue asking for a private contact channel without including exploit details or personal data.

Include the affected version, impact, reproduction using fictional data where possible, and relevant environment versions. Do not attach real device backups, identifiers, pairing records, app collections, screen observations, access tokens, or passcodes. Ordinary bugs and feature requests belong in public issues with redacted logs.

## Data handling

The tool itself does not call an AI API or collect telemetry. Your coding agent is a separate product with its own data handling and permission settings. Runtime directories are ignored by Git, but files copied elsewhere or deliberately force-added can still be published. Review the files you share.

A layout backup is not an encrypted full-device backup. Keep local backups private. Permission denials must be honored; changing transports is not a workaround for denied app access.

## Private artifact permissions

Version 0.1.1 creates sensitive files with POSIX mode `0600` and application-owned runtime directories with `0700`. Before accessing phone data, CLI commands repair existing `workspace/`, `backups/`, and `probe/` trees without changing file contents. The shared persistence APIs also secure their designated backup/opaque/UI directories. On macOS, extended ACL entries are removed through validated file descriptors, because mode bits alone do not revoke ACL grants.

Runtime directories must be dedicated to this tool. Symlinks, multiply linked files, foreign ownership, and special files are rejected rather than followed or silently modified. A permission-repair failure blocks the command. Explicit output files outside the runtime directories receive private permissions without changing existing shared parent directories. Public fictional demo assets use a separate ordinary export path.

This protects future access through these paths; it cannot undo prior reads, copied data, pre-existing open handles, or access by the same OS account or an administrator. Review older files copied outside managed runtime directories separately.
