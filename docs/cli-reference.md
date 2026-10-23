# CLI reference

`ios-organizer` is a two-way SpringBoard icon state compiler and CLI tool for macOS built on `pymobiledevice3`. It connects to a USB-attached iPhone via `com.apple.springboardservices` and `com.apple.mobile.installation_proxy`, backs up your raw Home Screen state, decompiles it into a human- and LLM-friendly `workspace/layout.yaml` DSL, validates edits, and compiles/pushes layouts back to the device while preserving widgets, Smart Stacks, web clips, and app bookmarks byte-for-byte.

---

For the agent-first starting workflow, see the [README](../README.md).

For exact row/column placement, empty space, or creating native widgets, use the separate `ui plan`, `ui doctor`, `ui observe`, and `ui action` commands documented in [the native UI workflow](native-ui.md). Spatial plans cannot be pushed through the legacy layout compiler.

## Setup

### Requirements
- macOS with `usbmuxd` running (standard on macOS)
- Python 3.11+ (tested with Python 3.14)
- A data-capable USB cable connected to your iPhone

### Installation
```bash
python3 -m venv .venv
.venv/bin/pip install -e '.[dev]'
```

### USB Trust & Unlock Requirements
1. Connect your iPhone to your Mac via USB.
2. **Unlock your iPhone** so it is on the Home Screen.
3. If prompted with **"Trust This Computer?"** on the iPhone, tap **Trust** and enter your device passcode.
4. Keep the iPhone unlocked whenever running `status`, `pull`, `diff`, `push`, or `restore`.

---

## Shared onboarding and inventory

`guide` is an offline, versioned conversation contract. It does not change a phone, persist session state, or establish approval. Agents use it with the detailed workflow.

```bash
.venv/bin/python -m ios_organizer guide --stage connect
.venv/bin/python -m ios_organizer guide --stage preferences
.venv/bin/python -m ios_organizer inventory --layout workspace/layout.yaml --output workspace/app_inventory.md
```

Stages: `connect`, `inventory`, `preferences`, `preview`, `apply`, `verify`. Export inventories from the actual newly pulled file; `pull` may write `layout.new.yaml` when preserving an edited file. The report includes all source entries, folder subpages, and unassigned apps, and labels its source rather than claiming a new scan.

`preview --output FILE.png` produces a deterministic image; `.html` produces an interactive folder preview. Render both from the exact proposed file. For spatial designs, use `ui plan --layout FILE --preview FILE.png` or `.html`; these are previews, not phone input.

## CLI Commands

Run all commands via `.venv/bin/python -m ios_organizer <command>`:

### 1. `status`
Checks the USB connection and displays device metadata, page count, visible Home Screen apps, App Library-only apps, and the age of your most recent backup:
```bash
.venv/bin/python -m ios_organizer status
```

### 2. `pull`
Creates a timestamped raw backup in `backups/backup_<YYYYMMDD_HHMMSS>.json` (plus `.meta.json` sidecar), extracts opaque elements to `workspace/.opaque/<token>.json`, and writes `workspace/layout.yaml`.
- If `workspace/layout.yaml` has been edited since the last pull, `pull` refuses to overwrite it and writes `workspace/layout.new.yaml` instead unless `--overwrite` is passed:
```bash
.venv/bin/python -m ios_organizer pull
.venv/bin/python -m ios_organizer pull --overwrite
```

### 3. `diff`
Validates and compiles `workspace/layout.yaml` and prints a structured human-readable diff against the connected iPhone's current layout (dock changes, page changes, folder changes, and moved apps) without writing to the device:
```bash
.venv/bin/python -m ios_organizer diff
```

### Offline validation and visual preview

Check the local YAML and opaque references, then generate a self-contained HTML preview:
```bash
.venv/bin/python -m ios_organizer validate
.venv/bin/python -m ios_organizer preview --open

# Review a sorting proposal
.venv/bin/python -m ios_organizer preview --layout workspace/layout_sorted.yaml --open
```

`preview` writes `workspace/layout_preview.html`. It shows page order, an approximate icon grid, widget sizes, the dock, expandable folder contents with pagination, and App Library warnings. It uses initials for app icons and loads no external assets. Both commands run without connecting to a phone. Installed-app availability is checked by the connected-device `diff` and `push` commands.

Use `--output <path>` to choose the HTML destination, `--columns 4 --rows 6` to set the preview grid, and `--opaque-dir <path>` to use another opaque object directory. Hidden/Focus status and exact SpringBoard placement are unavailable in the preview.

### 4. `push`
Backs up the current device state first, validates `workspace/layout.yaml` (blocking on errors unless `--force` is passed), compiles to raw SpringBoard format, shows the planned diff and App Library warning, asks for confirmation (unless `--yes`), pushes via `set_icon_state()`, and re-pulls from the same device to verify the result. Duplicate `displayIdentifier` values and malformed folder pagination are always blocked, including with `--force`:
```bash
# Dry run: validate, compile, show diff, and write workspace/compiled_preview.json (no device write)
.venv/bin/python -m ios_organizer push --dry-run

# Live push with interactive confirmation
.venv/bin/python -m ios_organizer push

# Live push without confirmation prompt
.venv/bin/python -m ios_organizer push --yes
```

Push and restore verification compare page order (including empty pages), folder page boundaries, and complete opaque objects. Transient primary-app metadata such as display names, versions, and modification dates is ignored. Exit status `0` means verification matched, `1` means an error or unverifiable result, and `2` means the write was sent but the re-pulled layout differs. Dry runs and user cancellation also return `0`. Failures and mismatches show the pre-write backup recovery command; recovery remains an explicit action.

**App Library limitation:** On the tested iOS version, SpringBoard places installed apps omitted from a pushed layout into available Home Screen slots. `push`, including `--dry-run`, and `restore` show the affected installed apps before confirmation. Listing an app under `unassigned_apps` does not preserve App Library-only placement. This can also affect recovery from a backup.

### 5. `restore`
Lists all timestamped backups in `backups/` or restores a chosen backup to the device. Always backs up the current device state before restoring and warns if the backup came from a different device UDID or iOS version:
```bash
# List available backups (and choose interactively)
.venv/bin/python -m ios_organizer restore

# Restore a specific backup file directly
.venv/bin/python -m ios_organizer restore backup_20261021_101440.json
```

### 6. `auto-sort`
Generates a sorted layout proposal in `workspace/layout_sorted.yaml` **without** modifying `workspace/layout.yaml` or touching the device:
```bash
# Initialize workspace/categories.yaml with all installed apps
.venv/bin/python -m ios_organizer auto-sort --init-categories

# Sort by category (uses workspace/categories.yaml -> device genre -> 'Other' folder)
.venv/bin/python -m ios_organizer auto-sort --mode category

# Sort loose apps and folder contents alphabetically
.venv/bin/python -m ios_organizer auto-sort --mode alpha

# Include App Library-only apps and dissolve/reorganize existing folders
.venv/bin/python -m ios_organizer auto-sort --mode category --include-unassigned --reorganize-folders
```

---

## `workspace/layout.yaml` Format

```yaml
# Generated by ios-organizer from backup_20261021_101440.json
# Device: Example iPhone (iOS TEST-OS)
schema_version: 1

dock:
  - "Phone (com.apple.mobilephone)"
  - "Messages (com.apple.MobileSMS)"
  - "Safari (com.apple.mobilesafari)"

pages:
  - page: 1
    items:
      - "@widget:a3b54bca6b7a:Smart Stack [Sleep, Weather] (small)"
      - "Camera (com.apple.camera)"
      - folder: "Productivity"
        apps:
          - "Calendar (com.apple.mobilecal)"
          - "Notes (com.apple.mobilenotes)"

unassigned_apps:
  - "Chrome (com.google.chrome.ios)"
```

### Key Rules
- **Apps**: Written as `"Display Name (bundle.id)"` or a bare `"bundle.id"`. Display names are cosmetic; the compiler matches strictly on the final `(bundle.id)`.
- **Folders**: Flat lists under `apps:`. New pulls include `page_sizes: [8, 6]`, for example, to preserve the original folder page boundaries, including empty folder pages. If you add or remove folder apps, update `page_sizes` so its sum equals the app count, or remove it to deliberately repaginate into pages of up to 9 apps. Layouts created by earlier versions have no `page_sizes` and retain automatic pagination; pull a fresh layout to capture original boundaries.
- **Empty Home Screen pages**: Preserved in their original positions during compilation and verification.
- **Opaque Elements (`@widget:...`, `@webclip:...`)**: Represent widgets, Smart Stacks, web clips, and secondary app bookmarks (`iconType: "app"`). You can move them between pages, and their exact raw dictionaries are re-inserted byte-for-byte from `workspace/.opaque/<token>.json`.
- **Observed iOS `setIconState` Behavior for `unassigned_apps`**: The legacy `com.apple.springboardservices` `setIconState` plist format does not have a separate App Library container; when any layout is pushed via `set_icon_state()`, iOS automatically places installed apps that were omitted from the pushed layout into the first available slots on existing Home Screen pages (and `ios-organizer push` reports these placements during its post-push verification diff).

---

## Recovery with `restore`

Every `pull`, `push`, and `restore` command creates a timestamped backup in `backups/backup_<YYYYMMDD_HHMMSS>.json` alongside a `.meta.json` sidecar before making any change. To roll back your Home Screen to any previous state:

1. List your backups:
   ```bash
   .venv/bin/python -m ios_organizer restore
   ```
2. Restore the desired backup:
   ```bash
   .venv/bin/python -m ios_organizer restore backups/backup_<YYYYMMDD_HHMMSS>.json
   ```
3. Refresh `workspace/layout.yaml` from the restored phone state:
   ```bash
   .venv/bin/python -m ios_organizer pull --overwrite
   ```

---

## Running Unit Tests

All compiler, validation, backup, and organizer tests run offline without a connected device:
```bash
.venv/bin/pytest -v
```
