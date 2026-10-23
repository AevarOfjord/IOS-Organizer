"""Device communication layer for ios-organizer.

This is the ONLY module in ios_organizer that imports pymobiledevice3.
It wraps usbmux device discovery, lockdownd pairing/session management,
SpringBoardServicesService (get/set icon state), and InstallationProxyService.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
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
    SetProhibitedError,
    StartServiceError,
    UserDeniedPairingError,
)
from pymobiledevice3.lockdown import UsbmuxLockdownClient, create_using_usbmux
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.springboard import SpringBoardServicesService
from pymobiledevice3.usbmux import list_devices

from ios_organizer.screen import ScreenAction, png_size, send_touch


class IOSDeviceError(Exception):
    """Base class for all ios-organizer device communication errors."""


class DeviceNotFound(IOSDeviceError):
    """Raised when no matching USB-connected iPhone is found."""


class MultipleDevices(IOSDeviceError):
    """Raised when multiple USB devices are connected and no --udid was provided."""

    def __init__(self, udids: list[str]) -> None:
        self.udids = udids
        joined = ", ".join(udids)
        super().__init__(
            f"Multiple USB iOS devices connected ({len(udids)} found: {joined}). "
            "Pass --udid <UDID> to select one."
        )


class NotPaired(IOSDeviceError):
    """Raised when the device is not paired or trust was not granted."""


class DeviceLocked(IOSDeviceError):
    """Raised when the device is passcode-locked and a service cannot start."""


class PushRejected(IOSDeviceError):
    """Raised when SpringBoard rejects or fails during set_icon_state."""


# Internal iOS system helper bundles that do not have "hidden" in SBAppTags
# on tested devices but are not user-launchable Home Screen / App Library apps.
_INTERNAL_SYSTEM_BUNDLES: frozenset[str] = frozenset(
    {
        "com.apple.FTMInternal",
        "com.apple.PreviewShell",
        "com.apple.sidecar",
        "com.apple.webapp",
        "com.apple.appleseed.FeedbackAssistant",
        "com.apple.NanoUniverse.AegirProxyApp",
        "com.apple.TVRemoteApp",
    }
)
_INTERNAL_SYSTEM_PREFIXES: tuple[str, ...] = ("com.apple.Posters.",)


@dataclass(frozen=True)
class DeviceInfo:
    """Metadata describing the connected iOS device."""

    name: str
    model: str
    product_type: str
    udid: str
    ios_version: str
    build_version: str
    icon_metrics: dict[str, float] = field(default_factory=dict)

    @property
    def page_capacity(self) -> int:
        cols = int(self.icon_metrics.get("homeScreenIconColumns", 4))
        rows = int(self.icon_metrics.get("homeScreenIconRows", 6))
        return cols * rows if (cols > 0 and rows > 0) else 24

    @property
    def dock_capacity(self) -> int:
        dock_max = int(self.icon_metrics.get("homeScreenIconDockMaxCount", 4))
        return dock_max if dock_max > 0 else 4

    @property
    def folder_page_capacity(self) -> int:
        cols = int(self.icon_metrics.get("homeScreenIconFolderColumns", 3))
        rows = int(self.icon_metrics.get("homeScreenIconFolderRows", 3))
        return cols * rows if (cols > 0 and rows > 0) else 9


def extract_installed_apps_map(
    raw_apps: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    """Transform InstallationProxyService raw output into a normalized app map.

    Returns:
        {bundle_id: {"name": str, "type": "User" | "System", "genre": str | None}}
    """
    catalog: dict[str, dict[str, Any]] = {}
    for bundle_id, info in sorted(raw_apps.items()):
        app_type_raw = str(info.get("ApplicationType") or "User")
        app_type = "System" if app_type_raw == "System" else "User"

        # Filter out internal/hidden system services & daemons
        tags = list(info.get("SBAppTags") or []) + list(info.get("SBAppTags~iphone") or [])
        if "hidden" in tags or info.get("UIApplicationLaunchProhibited"):
            continue
        if app_type == "System":
            if bundle_id in _INTERNAL_SYSTEM_BUNDLES or bundle_id.startswith(_INTERNAL_SYSTEM_PREFIXES):
                continue

        # Display name priority: CFBundleDisplayName -> CFBundleName -> bundle_id
        display_name = info.get("CFBundleDisplayName")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = info.get("CFBundleName")
        if not isinstance(display_name, str) or not display_name.strip():
            display_name = bundle_id
        else:
            # Strip C0/C1 control characters (e.g. U+0092 in '\x92Civ VI') and normalize NBSP
            cleaned = "".join(
                " " if c == "\xa0" else c
                for c in display_name
                if not (ord(c) < 32 or 127 <= ord(c) <= 159)
            ).strip()
            display_name = cleaned or bundle_id

        # Best-effort genre extraction
        genre: str | None = None
        raw_genre = info.get("Genre")
        if isinstance(raw_genre, str) and raw_genre.strip():
            genre = raw_genre.strip()
        else:
            sb_genres = info.get("SBMatchingApplicationGenres")
            if isinstance(sb_genres, list) and sb_genres:
                first_genre = sb_genres[0]
                if isinstance(first_genre, str) and first_genre.strip():
                    genre = first_genre.strip()

        catalog[bundle_id] = {
            "name": display_name,
            "type": app_type,
            "genre": genre,
        }
    return catalog


async def _resolve_usb_serial(udid: str | None = None) -> str:
    """Find the target USB device serial or raise a tool-level error."""
    try:
        all_devices = await list_devices()
    except (ConnectionFailedToUsbmuxdError, PermissionError, OSError, MuxException) as exc:
        raise DeviceNotFound(
            f"Could not communicate with macOS usbmuxd ({exc}). "
            "Ensure your iPhone is plugged in via USB and usbmuxd is accessible."
        ) from exc

    usb_devices = [d for d in all_devices if d.is_usb]
    if not usb_devices:
        net_count = sum(1 for d in all_devices if d.is_network)
        hint = f" ({net_count} Wi-Fi device(s) ignored; USB is required.)" if net_count else ""
        raise DeviceNotFound(
            f"No USB-connected iPhone detected.{hint} "
            "Connect your iPhone with a data-capable USB cable and unlock it."
        )

    if udid is not None:
        matches = [d for d in usb_devices if d.matches_udid(udid)]
        if not matches:
            available = ", ".join(d.serial for d in usb_devices)
            raise DeviceNotFound(
                f"Device with UDID '{udid}' not found among connected USB devices "
                f"(available: {available})."
            )
        return matches[0].serial

    if len(usb_devices) > 1:
        raise MultipleDevices([d.serial for d in usb_devices])

    return usb_devices[0].serial


async def _open_lockdown(
    udid: str | None = None,
    pair_timeout: float = 15.0,
) -> UsbmuxLockdownClient:
    """Connect to lockdownd over USB and translate pairing/lock errors."""
    serial = await _resolve_usb_serial(udid)
    try:
        return await create_using_usbmux(
            serial=serial,
            connection_type="USB",
            autopair=True,
            pair_timeout=pair_timeout,
        )
    except UserDeniedPairingError as exc:
        raise NotPaired(
            "The 'Trust This Computer?' prompt was declined on the iPhone. "
            "Reconnect the USB cable, unlock the iPhone, tap 'Trust', and enter your passcode."
        ) from exc
    except PairingDialogResponsePendingError as exc:
        raise NotPaired(
            "Pairing is waiting for approval on the iPhone screen. "
            "Tap 'Trust' on the 'Trust This Computer?' dialog and enter your passcode."
        ) from exc
    except (PasswordRequiredError, PasscodeRequiredError) as exc:
        raise DeviceLocked(
            "The iPhone is locked with a passcode. Unlock your iPhone and try again."
        ) from exc
    except (
        NotPairedError,
        NotTrustedError,
        InvalidHostIDError,
        PairingError,
        FatalPairingError,
    ) as exc:
        raise NotPaired(
            f"Device pairing is not trusted ({type(exc).__name__}). "
            "Unlock your iPhone, accept the 'Trust This Computer?' prompt, and enter your passcode."
        ) from exc
    except (NoDeviceConnectedError, DeviceNotFoundError, ConnectionFailedError) as exc:
        raise DeviceNotFound(
            f"USB connection to the iPhone failed ({exc}). Check the cable and try again."
        ) from exc
    except LockdownError as exc:
        msg = str(exc)
        if "PasswordProtected" in msg or "Passcode" in msg:
            raise DeviceLocked(
                "The iPhone is locked with a passcode. Unlock your iPhone and try again."
            ) from exc
        raise NotPaired(f"Lockdown error while connecting to iPhone: {exc}") from exc


def _translate_service_error(exc: Exception, operation: str) -> IOSDeviceError:
    """Translate a Lockdown/SpringBoard/InstallationProxy exception into a tool error."""
    if isinstance(exc, IOSDeviceError):
        return exc
    msg = str(exc)
    if isinstance(exc, (PasswordRequiredError, PasscodeRequiredError)) or "PasswordProtected" in msg:
        return DeviceLocked(
            f"The iPhone is locked (cannot perform {operation}). Unlock your iPhone and try again."
        )
    if isinstance(
        exc,
        (
            NotPairedError,
            NotTrustedError,
            InvalidHostIDError,
            PairingError,
            FatalPairingError,
            UserDeniedPairingError,
            PairingDialogResponsePendingError,
        ),
    ):
        return NotPaired(
            f"Device trust is required to {operation} ({exc}). "
            "Unlock your iPhone and accept the trust prompt."
        )
    if isinstance(exc, (NoDeviceConnectedError, DeviceNotFoundError, ConnectionFailedError)):
        return DeviceNotFound(f"Device disconnected during {operation}: {exc}")
    return IOSDeviceError(f"Device error during {operation}: {exc}")


async def _read_device_info(lockdown: UsbmuxLockdownClient) -> DeviceInfo:
    name = str(lockdown.all_values.get("DeviceName") or "iPhone")
    product_type = str(lockdown.product_type or "Unknown")
    model = str(lockdown.display_name or product_type)
    resolved_udid = str(lockdown.udid or lockdown.identifier or "Unknown")
    ios_version = str(lockdown.product_version or "Unknown")
    build_version = str(lockdown.product_build_version or "Unknown")

    metrics: dict[str, float] = {}
    try:
        async with SpringBoardServicesService(lockdown) as sb:
            raw_metrics = await sb.get_homescreen_icon_metrics()
            if isinstance(raw_metrics, dict):
                metrics = {
                    str(k): float(v)
                    for k, v in raw_metrics.items()
                    if isinstance(v, (int, float))
                }
    except Exception:
        metrics = {}

    return DeviceInfo(
        name=name,
        model=model,
        product_type=product_type,
        udid=resolved_udid,
        ios_version=ios_version,
        build_version=build_version,
        icon_metrics=metrics,
    )


async def _async_get_device_info(udid: str | None = None) -> DeviceInfo:
    lockdown = await _open_lockdown(udid=udid)
    async with lockdown:
        return await _read_device_info(lockdown)


async def _async_get_raw_icon_state(udid: str | None = None) -> list[Any]:
    lockdown = await _open_lockdown(udid=udid)
    async with lockdown:
        try:
            async with SpringBoardServicesService(lockdown) as sb:
                return await sb.get_icon_state(format_version="2")
        except Exception as exc:
            raise _translate_service_error(exc, "get_icon_state") from exc


async def _async_set_raw_icon_state(state: list[Any], udid: str | None = None) -> None:
    lockdown = await _open_lockdown(udid=udid)
    async with lockdown:
        try:
            async with SpringBoardServicesService(lockdown) as sb:
                await sb.set_icon_state(newstate=state)
        except (PasswordRequiredError, PasscodeRequiredError) as exc:
            raise DeviceLocked(
                "The iPhone is locked; cannot push icon state. Unlock the iPhone and try again."
            ) from exc
        except (SetProhibitedError, StartServiceError, LockdownError, PyMobileDevice3Exception, Exception) as exc:
            msg = str(exc)
            if "PasswordProtected" in msg:
                raise DeviceLocked(
                    "The iPhone is locked; cannot push icon state. Unlock the iPhone and try again."
                ) from exc
            raise PushRejected(f"SpringBoard rejected set_icon_state: {exc}") from exc


async def _async_get_installed_apps(udid: str | None = None) -> dict[str, dict[str, Any]]:
    lockdown = await _open_lockdown(udid=udid)
    async with lockdown:
        try:
            async with InstallationProxyService(lockdown) as iproxy:
                raw_apps = await iproxy.get_apps(application_type="Any", show_placeholders=True)
        except Exception as exc:
            raise _translate_service_error(exc, "get_apps") from exc
        return extract_installed_apps_map(raw_apps)


async def _async_fetch_snapshot(
    udid: str | None = None,
) -> tuple[DeviceInfo, list[Any], dict[str, dict[str, Any]]]:
    lockdown = await _open_lockdown(udid=udid)
    async with lockdown:
        try:
            info = await _read_device_info(lockdown)
            async with SpringBoardServicesService(lockdown) as sb:
                raw_state = await sb.get_icon_state(format_version="2")
            async with InstallationProxyService(lockdown) as iproxy:
                raw_apps = await iproxy.get_apps(application_type="Any", show_placeholders=True)
            installed_apps = extract_installed_apps_map(raw_apps)
            return info, raw_state, installed_apps
        except Exception as exc:
            raise _translate_service_error(exc, "fetch_device_snapshot") from exc


def get_device_info(udid: str | None = None) -> DeviceInfo:
    """Connect to the iPhone and return its DeviceInfo."""
    return asyncio.run(_async_get_device_info(udid=udid))


def get_raw_icon_state(udid: str | None = None) -> list[Any]:
    """Fetch the raw SpringBoard icon state list (format_version='2')."""
    return asyncio.run(_async_get_raw_icon_state(udid=udid))


def set_raw_icon_state(state: list[Any], udid: str | None = None) -> None:
    """Push a raw SpringBoard icon state list to the device."""
    asyncio.run(_async_set_raw_icon_state(state=state, udid=udid))


def get_installed_apps(udid: str | None = None) -> dict[str, dict[str, Any]]:
    """Return {bundle_id: {'name': str, 'type': 'User'|'System', 'genre': str|None}}."""
    return asyncio.run(_async_get_installed_apps(udid=udid))


def fetch_device_snapshot(
    udid: str | None = None,
) -> tuple[DeviceInfo, list[Any], dict[str, dict[str, Any]]]:
    """Fetch DeviceInfo, raw icon state, and installed apps in a single lockdown session."""
    return asyncio.run(_async_fetch_snapshot(udid=udid))


UI_SETUP_HINT = (
    'Native screen control requires Developer Mode enabled by the owner, a mounted '
    'Developer Disk Image, and an unlocked USB-connected iPhone. '
    'This command does not enable Developer Mode, install images, or remove passcodes.'
)


def get_ui_capabilities(udid: str) -> dict[str, Any]:
    """Read prerequisite state; availability is not an end-to-end feature test."""
    async def check():
        async with await _open_lockdown(udid=udid, pair_timeout=0) as lockdown:
            enabled = await lockdown.get_developer_mode_status()
            return {'udid': lockdown.udid, 'developer_mode': bool(enabled),
                    'screenshot': 'not_probed', 'touch_input': 'not_probed',
                    'sparse_placement': 'requires_visual_verification',
                    'widget_creation': 'agent_guided_gallery_workflow',
                    'next_step': 'Run ui observe to test developer-image and capture readiness.' if enabled else UI_SETUP_HINT}
    try:
        return asyncio.run(asyncio.wait_for(check(), timeout=20))
    except IOSDeviceError:
        raise
    except Exception as exc:
        raise IOSDeviceError(f'UI prerequisite check failed: {exc}. {UI_SETUP_HINT}') from exc


@asynccontextmanager
async def _ui_connection(udid: str):
    """Explicit USB device only; use the dependency's no-root in-process tunnel."""
    from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel

    async with await _open_lockdown(udid=udid, pair_timeout=0) as lockdown:
        canonical_udid = lockdown.udid
        if not await lockdown.get_developer_mode_status():
            raise IOSDeviceError(f'Developer Mode is not enabled. {UI_SETUP_HINT}')
    async with UserspaceRsdTunnel(serial=canonical_udid, autopair=False, remotepairing_fallback=False) as rsd:
        if rsd.udid.replace('-', '') != canonical_udid.replace('-', ''):
            raise IOSDeviceError('Developer tunnel connected to a different device; no input sent.')
        yield rsd, canonical_udid


async def _capture_ui(rsd: Any, udid: str) -> dict[str, Any]:
    from pymobiledevice3.remote.core_device.screen_capture_service import ScreenCaptureService

    async with ScreenCaptureService(rsd) as capture:
        result = await capture.capture_screenshot()
    image = result.get('image')
    if not isinstance(image, bytes):
        raise IOSDeviceError('Screen capture returned no PNG image.')
    width, height = png_size(image)
    return {'udid': udid, 'width': width, 'height': height, 'image': image}


async def _async_observe_ui(udid: str) -> dict[str, Any]:
    async with _ui_connection(udid) as (rsd, canonical):
        return await _capture_ui(rsd, canonical)


def observe_ui(udid: str) -> dict[str, Any]:
    """Read a native screenshot without delivering input or changing device settings."""
    try:
        return asyncio.run(asyncio.wait_for(_async_observe_ui(udid), timeout=40))
    except IOSDeviceError:
        raise
    except (Exception, asyncio.TimeoutError) as exc:
        raise IOSDeviceError(f'Screen observation failed: {exc}. {UI_SETUP_HINT}') from exc


async def _async_ui_action(udid: str, action: ScreenAction, width: int, height: int) -> dict[str, Any]:
    from pymobiledevice3.remote.core_device.hid_service import (
        ASCII_TO_HID, KEY_LEFT_SHIFT, IndigoHIDService, touch_session,
    )

    async with _ui_connection(udid) as (rsd, canonical):
        current = await _capture_ui(rsd, canonical)
        if (current['width'], current['height']) != (width, height):
            raise IOSDeviceError('Screen dimensions changed. Observe again before sending input.')
        if action.kind == 'home':
            async with IndigoHIDService(rsd) as hid:
                try:
                    await hid.send_button(0x0C, 0x40, 1)
                    await asyncio.sleep(0.05)
                finally:
                    await hid.send_button(0x0C, 0x40, 2)
        else:
            async with touch_session(rsd) as hid:
                if action.kind == 'type':
                    keyboard = await hid.create_keyboard_service()
                    try:
                        for character in action.text:
                            usage, shift = ASCII_TO_HID[character]
                            if shift:
                                await hid.send_keyboard(keyboard, (KEY_LEFT_SHIFT,))
                                await asyncio.sleep(0.04)
                            await hid.send_keyboard(keyboard, (KEY_LEFT_SHIFT, usage) if shift else (usage,))
                            await asyncio.sleep(0.04)
                            await hid.send_keyboard(keyboard, ())
                            await asyncio.sleep(0.02)
                    finally:
                        await hid.send_keyboard(keyboard, ())
                else:
                    await send_touch(hid, action, width, height)
        await asyncio.sleep(0.35)
        return await _capture_ui(rsd, canonical)


def perform_ui_action(udid: str, action: ScreenAction, width: int, height: int) -> dict[str, Any]:
    """Send one observed action, then capture the result. Never retry input automatically."""
    action.validate(width, height)
    try:
        return asyncio.run(asyncio.wait_for(_async_ui_action(udid, action, width, height), timeout=45))
    except (Exception, asyncio.TimeoutError) as exc:
        raise IOSDeviceError(
            f'UI action did not finish with a result screenshot: {exc}. '
            f'Input may have been delivered. Observe again before deciding what to do. {UI_SETUP_HINT}'
        ) from exc
