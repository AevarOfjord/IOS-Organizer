"""Shared, offline conversation guide and inventory export for any coding agent."""

from __future__ import annotations

from enum import Enum
from html import escape
from typing import Any

from ios_organizer.compiler import is_opaque_entry, parse_app_entry, parse_opaque_entry


class Stage(str, Enum):
    connect = "connect"
    inventory = "inventory"
    preferences = "preferences"
    preview = "preview"
    apply = "apply"
    verify = "verify"


GUIDE = {
    "connect": {
        "message": "I'll help organize your iPhone and handle the setup and commands. Connect it to this Mac with a USB cable, unlock it, and accept Trust This Computer if prompted. I'll save a backup and show your app list before asking what to change. Your Home Screen stays as it is until you approve a preview.",
        "tasks": ["Read AGENTS.md and docs/agent-workflow.md", "Prepare the local Python environment", "Run status; identify the target if multiple phones are connected", "Run pull and use its reported current layout path"],
        "done_when": "The target phone is identified and a fresh backup and layout are available.",
    },
    "inventory": {
        "message": "I've saved your current layout. Let's look at all your apps before deciding what to change.",
        "tasks": ["Run inventory against the freshly pulled layout", "Show the full Markdown app list, including folders and App Library-only entries", "Identify widgets, web clips, and additional icons separately", "Report the catalog scope, backup, and source layout"],
        "done_when": "The user can open the complete current inventory; older files are clearly labeled.",
    },
    "preferences": {
        "message": "We can tidy your pages, group similar apps into folders, or redesign your main page. You can describe what you want or ask me to recommend a setup.",
        "questions": [
            "What would you like to improve: pages, folders, your main page, or a combination?",
            "What must stay exactly where it is, including the bottom row of apps?",
            "Should folders group apps by function, provider, or a mix? Any custom groups?",
            "Should folders and apps be alphabetical? Fill pages or leave room?",
            "Keep your current Home Screen selection or bring all apps onto pages?",
        ],
        "tasks": ["Ask only the first one or two unanswered questions, then wait", "Reuse preferences already given", "Explain that USB writes may bring App Library-only apps onto pages", "Record preferences under workspace/"],
        "done_when": "The goal, protected areas, grouping, order, and coverage are settled sufficiently to plan.",
    },
    "preview": {
        "message": "I'll prepare a proposed layout, an image preview, and the full folder list for you to review.",
        "tasks": ["Build a separate proposal from the fresh baseline", "Account for every app and opaque object and preserve agreed areas", "Validate and generate both PNG and HTML previews from the exact proposal", "Display the PNG inline and link the HTML and complete folder contents", "For widgets or gaps, read docs/native-ui.md and disclose the available route", "Run a device dry run for a bulk layout and resolve differences"],
        "done_when": "The exact proposal is validated and visible; wait for approval before applying.",
    },
    "apply": {
        "message": "Once you approve this layout, I'll back up your phone again, apply it, and check the result.",
        "tasks": ["Establish explicit user approval of the current reviewed proposal", "Apply the approved file to the identified device using the CLI", "For spatial changes, use approved Mirroring tools or explain manual steps", "Keep Developer Mode optional; honor a refusal"],
        "done_when": "The authorized change has been attempted once and its actual result is available for verification.",
    },
    "verify": {
        "message": "I'll check the result against the approved layout and give you the recovery backup location.",
        "tasks": ["For USB changes, require an actual write and Verification passed", "For spatial changes, inspect every requested cell, widget, and empty region after edit mode", "Report mismatches or pending manual steps accurately", "Save the result and backup path; avoid automatic replay or restore"],
        "done_when": "The user knows what was verified, what remains pending, and where the recovery backup is.",
    },
}


def guide(stage: Stage) -> dict[str, Any]:
    """A versioned guide, not persistent state or evidence of human approval."""
    return {"workflow_version": 1, "stage": stage.value, "developer_mode_required": False,
            "enforcement": "Agent guidance; does not authorize or execute device writes.", **GUIDE[stage.value]}


def _label(value: Any) -> str:
    text = escape(" ".join(str(value).split()))
    for char in "\\`*_[]#|":
        text = text.replace(char, "\\" + char)
    return text


def render_inventory(layout: dict[str, Any], *, source: str) -> str:
    """Export every entry, including folder subpages, without accessing a phone."""
    ids: set[str] = set()

    def items(entries: list, level: int = 3) -> list[str]:
        lines = []
        for entry in entries:
            if isinstance(entry, dict) and "folder" in entry:
                lines += [f"{'#' * level} Folder: {_label(entry['folder'])}", ""]
                apps = entry.get("apps") or []
                sizes = entry.get("page_sizes") or [len(apps)]
                offset = 0
                for number, size in enumerate(sizes, 1):
                    if len(sizes) > 1:
                        lines += [f"Folder page {number}:", ""]
                    lines += items(apps[offset:offset + size], level + 1) or ["_Empty_", ""]
                    offset += size
            elif is_opaque_entry(entry):
                ref = parse_opaque_entry(entry)
                lines.append(f"- Additional icon / {ref.kind}: {_label(ref.description)} (reference: {_label(ref.token)})")
            else:
                name, bid = parse_app_entry(entry)
                ids.add(bid)
                lines.append(f"- {_label(name)} — {_label(bid)}")
        return lines + ([""] if lines else [])

    body = ["## Dock", ""] + (items(layout.get("dock") or []) or ["_Empty_", ""])
    for number, page in enumerate(layout.get("pages") or [], 1):
        body += [f"## Page {page.get('page', number)}", ""]
        body += items(page.get("items") or []) or ["_Empty page_", ""]
    unassigned = layout.get("unassigned_apps") or []
    body += ["## App Library-only / unassigned apps", ""]
    body += items(unassigned) or ["_None in this snapshot_", ""]
    return "\n".join([
        "# App inventory", "", f"Source layout: {_label(source)}", "",
        f"{len(ids)} unique app IDs · {len(layout.get('pages') or [])} pages · {len(unassigned)} unassigned apps", "",
        "This report describes the source file, not a new phone scan. Use a fresh pull for a current inventory. "
        "The device catalog includes user-facing apps and offloaded placeholders; hidden system services are excluded. "
        "Widgets, web clips, and additional app bookmarks are listed separately and do not increase the unique app count.", "",
        *body,
    ])
