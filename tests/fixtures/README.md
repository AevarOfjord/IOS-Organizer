# Synthetic fixtures

Both JSON fixtures are fabricated. Neither is a sanitized device export.

- `complex_synthetic_state.json` contains 244 generated `com.example.synthetic` apps, twelve folders with deliberately unusual page boundaries, an empty Home Screen page, a widget, an app bookmark, and a web clip.
- `synthetic_edge_cases.json` contains fictional identifiers and payloads exercising offloaded apps, opaque objects, property-list bytes/dates, and multi-page folders. Standard Apple bundle IDs are examples.

Keep real app collections, phone identifiers, backups, and screenshots in the ignored runtime directories.
