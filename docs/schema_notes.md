# SpringBoard and InstallationProxy observations

These are development observations from a single test configuration, not a guarantee for other devices or releases. Device model, operating system version, and build details are intentionally omitted. All identifiers, timestamps, labels, and payload examples below are fictional. No device export is included in this repository.

## Layout and capacity

`get_icon_state(format_version="2")` returned a list of lists. The first list is the dock; subsequent lists are Home Screen pages in order. Pages can contain app icons, folders, widgets/Smart Stacks, web clips, and additional app bookmark icons.

The observed metrics were four columns and six rows, up to fifteen Home Screen pages, four dock items, and three-by-three folder pages with up to fifteen subpages. Read actual metrics rather than treating these as universal limits.

Hidden/Focus pages were returned inline as ordinary page arrays, without a wrapper indicating visibility. Raw page one need not be the user's visible main page. Preserve page order and empty pages and identify the intended main page before planning changes.

## Primary app icons

```json
{
  "bundleIdentifier": "com.example.synthetic.mail",
  "bundleVersion": "1.0",
  "displayIdentifier": "com.example.synthetic.mail",
  "displayName": "Example Mail",
  "iconModDate": "2026-01-01T00:00:00"
}
```

A primary icon's `displayIdentifier` equals its bundle ID. `iconModDate` is a property-list datetime. Offloaded placeholders can contain only `displayIdentifier` and `displayName`; the other fields may be absent.

Call `InstallationProxyService.get_apps(application_type="Any", show_placeholders=True)` to include offloaded apps. Omitting placeholders can falsely report installed apps as missing during validation.

## Additional app bookmark icons

```json
{
  "bundleIdentifier": "com.example.synthetic.mail",
  "displayIdentifier": "11111111-2222-4333-8444-555555555555",
  "iconLists": [],
  "iconType": "app"
}
```

The same app can have additional placements with a distinct UUID `displayIdentifier`. SpringBoard requires globally unique display identifiers, even when several placements refer to the same bundle ID. The compiler treats these as opaque `@widget` bookmark references, preserving their identity and payload. They do not count as additional unique installed apps.

## Folders

```json
{
  "displayName": "Example Work",
  "iconLists": [["com.example.synthetic.mail"], [], ["com.example.synthetic.tasks"]],
  "listType": "folder"
}
```

Folder subpages can have fewer than nine entries, including empty subpages. Decompilation records boundaries in `page_sizes`; compilation preserves them. A newly created folder without `page_sizes` uses automatic pagination. Explicit sizes must be valid and sum to the entry count.

Extra folder metadata is preserved through an opaque token when present. Renaming or changing folder contents should not silently discard that metadata.

## Widgets and Smart Stacks

```json
{
  "allowsExternalSuggestions": false,
  "allowsSuggestions": true,
  "displayIdentifier": "22222222-3333-4444-8555-666666666666",
  "elements": [
    {
      "bundleIdentifier": "com.example.synthetic.weather.widget",
      "containerBundleIdentifier": "com.example.synthetic.weather",
      "elementType": "widget",
      "uniqueIdentifier": "33333333-4444-4555-8666-777777777777",
      "widgetIdentifier": "com.example.synthetic.weather"
    }
  ],
  "gridSize": "small",
  "iconLists": [],
  "iconType": "custom"
}
```

Widgets are opaque objects. A stack can contain multiple elements and an optional `userSelectedElementIdentifier`. Preserve raw data through `@widget:<token>:<description>` references instead of fabricating widget identities.

Capacity estimates use small 2×2, medium 4×2, large 4×4, and extra-large 4×6. Unknown sizes produce a warning. Capacity alone does not prove a widget will pack into a particular row/column arrangement. See [spatial designs](native-ui.md) for exact requests and native widget creation.

## Web clips and unknown elements

Web clips have `webClipURL`, a display identifier, and a title. They use `@webclip:<token>:<title>` references. Unknown dictionaries and typed property-list values are retained in the opaque store rather than discarded. Public examples use `example.com` and generated identifiers.

## Installed-app catalog

The observed catalog contained user apps, offloaded placeholders, user-facing system apps, and hidden/internal services. `device.py` filters hidden/internal services for the user-facing inventory. This inventory is not a list of every iOS process or service.

App-name priority is `CFBundleDisplayName`, then `CFBundleName`, then the bundle ID. Store genre fields were absent in the tested catalog. Some system apps provided `SBMatchingApplicationGenres`; it is a best-effort fallback. User app classification generally needs agent-maintained categories rather than a presumed App Store category.

## Observed USB write behavior

The legacy `setIconState` format has no separate App Library-only container. On the development configuration, omitted installed apps reappeared in available Home Screen slots after a write. Listing an app under `unassigned_apps` therefore does not preserve App Library-only placement.

When every installed app was accounted for, bulk writes were observed to read back with no normalized layout differences. Verification ignores transient primary-app metadata; this is not byte-for-byte equality of all raw data, proof of widget configuration, or proof of free spacing.

Modern gaps are not encoded by the compiler. A later bulk write can compact placement. Layout backups do not guarantee recovery of exact spacing, visibility/Focus status, or widget settings. Record these limitations before approval.
