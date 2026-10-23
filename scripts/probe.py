#!/usr/bin/env python3
"""Step 1 Discovery Probe for ios-organizer.

Connects to a USB-attached iPhone via pymobiledevice3, verifies lockdown pairing,
fetches the raw SpringBoard icon state once and installed app metadata once, and
writes discovery artifacts to probe/raw_icon_state.json and probe/app_fields.json.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import datetime
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from pymobiledevice3.exceptions import (
    ConnectionFailedError,
    ConnectionFailedToUsbmuxdError,
    DeviceNotFoundError,
    FatalPairingError,
    InvalidHostIDError,
    LockdownError,
    MuxException,
    NoDeviceConnectedError,
    NotPairedError,
    NotTrustedError,
    PairingDialogResponsePendingError,
    PairingError,
    PasscodeRequiredError,
    PasswordRequiredError,
    PyMobileDevice3Exception,
    StartServiceError,
    UserDeniedPairingError,
)
from pymobiledevice3.lockdown import create_using_usbmux
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.springboard import SpringBoardServicesService
from pymobiledevice3.usbmux import list_devices
from ios_organizer.private_io import secure_private_tree, write_private


def _json_default(obj: Any) -> Any:
    """Fallback serializer for plist types (datetime, bytes) when writing JSON."""
    if isinstance(obj, (datetime.datetime, datetime.date)):
        return {"__plist_type__": "datetime", "iso": obj.isoformat()}
    if isinstance(obj, bytes):
        return {
            "__plist_type__": "bytes",
            "base64": base64.b64encode(obj).decode("ascii"),
        }
    return repr(obj)


def _find_non_json_types(obj: Any, path: str = "$") -> list[dict[str, str]]:
    """Scan nested structure for non-standard JSON types (bytes, datetime, etc.)."""
    found: list[dict[str, str]] = []
    if isinstance(obj, dict):
        for k, v in obj.items():
            found.extend(_find_non_json_types(v, f"{path}.{k}"))
    elif isinstance(obj, list):
        for idx, item in enumerate(obj):
            found.extend(_find_non_json_types(item, f"{path}[{idx}]"))
    elif not isinstance(obj, (str, int, float, bool)) and obj is not None:
        found.append({"path": path, "type": type(obj).__name__})
    return found


async def run_probe(udid: str | None = None, pair_timeout: float = 15.0) -> int:
    # 1. Enumerate USB devices via usbmuxd
    try:
        all_mux_devices = await list_devices()
    except (ConnectionFailedToUsbmuxdError, PermissionError, OSError) as exc:
        print(
            f"[ERROR] Could not connect to usbmuxd (/var/run/usbmuxd): {exc}\n"
            "Action: Ensure macOS usbmuxd is running, you have socket permissions, "
            "and your iPhone is plugged in via USB.",
            file=sys.stderr,
        )
        return 1
    except MuxException as exc:
        print(
            f"[ERROR] USB multiplexer error: {exc}\n"
            "Action: Reconnect your iPhone via USB cable and try again.",
            file=sys.stderr,
        )
        return 1

    usb_devices = [d for d in all_mux_devices if d.is_usb]

    if not usb_devices:
        net_count = len([d for d in all_mux_devices if d.is_network])
        extra = (
            f" ({net_count} Wi-Fi device(s) seen, but USB is required.)"
            if net_count
            else ""
        )
        print(
            f"[ERROR] No USB-connected iOS device detected.{extra}\n"
            "Action: Connect your iPhone to this Mac with a data-capable USB cable, "
            "wake the screen, and unlock it.",
            file=sys.stderr,
        )
        return 1

    if udid:
        matching = [d for d in usb_devices if d.matches_udid(udid)]
        if not matching:
            available = ", ".join(d.serial for d in usb_devices)
            print(
                f"[ERROR] Requested UDID '{udid}' was not found among connected USB devices.\n"
                f"Available USB UDIDs: {available}\n"
                "Action: Pass a valid --udid from the list above.",
                file=sys.stderr,
            )
            return 1
        target_serial = matching[0].serial
    else:
        if len(usb_devices) > 1:
            available = "\n  - ".join(d.serial for d in usb_devices)
            print(
                f"[ERROR] Multiple USB devices connected ({len(usb_devices)} found):\n"
                f"  - {available}\n"
                "Action: Disconnect extra devices or specify one with: "
                "python scripts/probe.py --udid <UDID>",
                file=sys.stderr,
            )
            return 1
        target_serial = usb_devices[0].serial

    # 2. Establish lockdown session & verify pairing/trust
    try:
        lockdown = await create_using_usbmux(
            serial=target_serial,
            connection_type="USB",
            autopair=True,
            pair_timeout=pair_timeout,
        )
    except UserDeniedPairingError:
        print(
            "[ERROR] Trust prompt was declined on the iPhone ('Don't Trust' tapped).\n"
            "Action: Unplug and replug the USB cable, unlock the iPhone, tap 'Trust' on "
            "the 'Trust This Computer?' prompt, and enter your device passcode.",
            file=sys.stderr,
        )
        return 1
    except PairingDialogResponsePendingError:
        print(
            "[ERROR] Pairing is waiting for approval on the iPhone screen.\n"
            "Action: Look at your iPhone, tap 'Trust' on the 'Trust This Computer?' "
            "dialog, enter your passcode, and re-run this script.",
            file=sys.stderr,
        )
        return 1
    except (PasswordRequiredError, PasscodeRequiredError):
        print(
            "[ERROR] The iPhone is locked with a passcode.\n"
            "Action: Unlock your iPhone so it is on the Home Screen, keep it awake, "
            "and re-run this script.",
            file=sys.stderr,
        )
        return 1
    except (NotPairedError, NotTrustedError, InvalidHostIDError, PairingError, FatalPairingError) as exc:
        print(
            f"[ERROR] Device pairing is not trusted ({type(exc).__name__}: {exc}).\n"
            "Action: Unlock your iPhone, reconnect USB, accept the 'Trust This Computer?' "
            "prompt, and enter your passcode.",
            file=sys.stderr,
        )
        return 1
    except (NoDeviceConnectedError, DeviceNotFoundError, ConnectionFailedError) as exc:
        print(
            f"[ERROR] Lost USB connection to device ({exc}).\n"
            "Action: Check the USB cable connection and try again.",
            file=sys.stderr,
        )
        return 1

    async with lockdown:
        device_name = lockdown.all_values.get("DeviceName", "Unknown")
        model_display = lockdown.display_name or lockdown.product_type or "Unknown"
        product_type = lockdown.product_type or "Unknown"
        resolved_udid = lockdown.udid or lockdown.identifier or target_serial
        ios_version = lockdown.product_version
        build_version = lockdown.product_build_version or "Unknown"

        print("=== Connected iOS Device ===")
        print(f"  Device Name : {device_name}")
        print(f"  Model       : {model_display} ({product_type})")
        print(f"  UDID        : {resolved_udid}")
        print(f"  iOS Version : {ios_version} (Build {build_version})")
        print()

        probe_dir = Path("probe")
        secure_private_tree(probe_dir)

        # 3. Call get_icon_state() once
        try:
            async with SpringBoardServicesService(lockdown) as sb:
                raw_icon_state = await sb.get_icon_state(format_version="2")
                try:
                    icon_metrics = await sb.get_homescreen_icon_metrics()
                except Exception as metrics_exc:
                    icon_metrics = {"error": str(metrics_exc)}
        except (PasswordRequiredError, PasscodeRequiredError):
            print(
                "[ERROR] iPhone is locked; SpringBoard service could not start.\n"
                "Action: Unlock your iPhone and re-run this script.",
                file=sys.stderr,
            )
            return 1
        except (NotPairedError, StartServiceError, LockdownError, PyMobileDevice3Exception) as exc:
            err_text = str(exc)
            if "PasswordProtected" in err_text or "Passcode" in err_text:
                print(
                    "[ERROR] iPhone is locked (PasswordProtected).\n"
                    "Action: Unlock your iPhone and re-run this script.",
                    file=sys.stderr,
                )
                return 1
            print(
                f"[ERROR] Failed to query SpringBoardServicesService: {exc}\n"
                "Action: Ensure the iPhone is unlocked, trusted, and on the Home Screen.",
                file=sys.stderr,
            )
            return 1

        raw_state_path = probe_dir / "raw_icon_state.json"
        non_json_in_state = _find_non_json_types(raw_icon_state)
        write_private(raw_state_path,
            json.dumps(raw_icon_state, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            encoding="utf-8",
        )
        print(f"Saved raw icon state to {raw_state_path} (top-level length: {len(raw_icon_state)})")
        if non_json_in_state:
            print(f"  Note: {len(non_json_in_state)} non-standard JSON value(s) serialized via fallback.")

        # 4. Call get_apps() once (including placeholders/offloaded apps)
        try:
            async with InstallationProxyService(lockdown) as iproxy:
                all_apps = await iproxy.get_apps(application_type="Any", show_placeholders=True)
        except (PasswordRequiredError, PasscodeRequiredError):
            print(
                "[ERROR] iPhone is locked; InstallationProxyService could not start.\n"
                "Action: Unlock your iPhone and re-run this script.",
                file=sys.stderr,
            )
            return 1
        except (NotPairedError, StartServiceError, LockdownError, PyMobileDevice3Exception) as exc:
            print(
                f"[ERROR] Failed to query InstallationProxyService: {exc}\n"
                "Action: Ensure the iPhone is unlocked and trusted.",
                file=sys.stderr,
            )
            return 1

        key_counter: Counter[str] = Counter()
        type_counter: Counter[str] = Counter()
        genre_related_keys: set[str] = set()
        sample_user_apps: dict[str, Any] = {}
        sample_system_apps: dict[str, Any] = {}
        app_catalog_summary: dict[str, dict[str, Any]] = {}

        for bundle_id, info in sorted(all_apps.items()):
            app_type = str(info.get("ApplicationType", "Unknown"))
            type_counter[app_type] += 1
            for k in info.keys():
                key_counter[k] += 1
                if any(tok in k.lower() for tok in ("genre", "category", "itunes", "tag")):
                    genre_related_keys.add(k)

            summary_entry = {
                "ApplicationType": info.get("ApplicationType"),
                "CFBundleDisplayName": info.get("CFBundleDisplayName"),
                "CFBundleName": info.get("CFBundleName"),
                "CFBundleExecutable": info.get("CFBundleExecutable"),
                "Path": info.get("Path"),
                "SBAppTags": info.get("SBAppTags"),
                "SBAppTags~iphone": info.get("SBAppTags~iphone"),
                "SBMatchingApplicationGenres": info.get("SBMatchingApplicationGenres"),
                "UIApplicationLaunchProhibited": info.get("UIApplicationLaunchProhibited"),
                "SequenceNumber": info.get("SequenceNumber"),
                "IsDemotedApp": info.get("IsDemotedApp"),
                "IsAppClip": info.get("IsAppClip"),
                "UIFileSharingEnabled": info.get("UIFileSharingEnabled"),
                "Genre": info.get("Genre"),
                "GenreID": info.get("GenreID"),
                "iTunesMetadata": info.get("iTunesMetadata"),
            }
            app_catalog_summary[bundle_id] = summary_entry

            if app_type == "User" and len(sample_user_apps) < 5:
                sample_user_apps[bundle_id] = {
                    "keys": sorted(info.keys()),
                    "selected_fields": summary_entry,
                }
            elif app_type == "System" and len(sample_system_apps) < 5:
                sample_system_apps[bundle_id] = {
                    "keys": sorted(info.keys()),
                    "selected_fields": summary_entry,
                }

        app_fields_report = {
            "device": {
                "device_name": device_name,
                "model_display": model_display,
                "product_type": product_type,
                "udid": resolved_udid,
                "ios_version": ios_version,
                "build_version": build_version,
            },
            "icon_metrics": icon_metrics,
            "non_json_types_in_icon_state": non_json_in_state[:50],
            "total_apps": len(all_apps),
            "counts_by_application_type": dict(type_counter),
            "key_frequencies": dict(key_counter.most_common()),
            "genre_or_category_related_keys": sorted(genre_related_keys),
            "sample_user_apps": sample_user_apps,
            "sample_system_apps": sample_system_apps,
            "apps_summary": app_catalog_summary,
        }

        app_fields_path = probe_dir / "app_fields.json"
        write_private(app_fields_path,
            json.dumps(app_fields_report, indent=2, ensure_ascii=False, default=_json_default) + "\n",
            encoding="utf-8",
        )
        print(f"Saved app field analysis to {app_fields_path} (total apps: {len(all_apps)})")
        return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Probe USB-connected iPhone for SpringBoard & app schema.")
    parser.add_argument("--udid", help="Target device UDID if multiple devices are attached.", default=None)
    parser.add_argument(
        "--pair-timeout",
        type=float,
        default=15.0,
        help="Seconds to wait for user to accept Trust dialog on device.",
    )
    args = parser.parse_args()
    sys.exit(asyncio.run(run_probe(udid=args.udid, pair_timeout=args.pair_timeout)))


if __name__ == "__main__":
    main()
