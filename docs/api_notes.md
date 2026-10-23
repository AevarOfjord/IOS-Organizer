# `pymobiledevice3` API Verification Notes

## Environment & Pinned Versions
- **Python**: `3.14.8` (`.venv/bin/python`)
- **`pymobiledevice3`**: `11.41.2`
- **`pyyaml`**: `6.0.3`
- **`rich`**: `15.0.0`
- **`typer`**: `0.27.3`
- **`pytest`**: `9.1.1`

---

## Important Architectural Finding: Asyncio API
In `pymobiledevice3==11.41.2`, all lockdown, usbmux, and service methods are **asynchronous (`async def` coroutines)**. Synchronous wrappers can be exposed in `device.py` using `asyncio.run(...)` (or an internal async helper run via `asyncio.run`) so the rest of the CLI remains synchronous and clean.

---

## Verified Import Paths & Signatures

### 1. USB Device Enumeration (`pymobiledevice3.usbmux`)
- **Import**:
  ```python
  from pymobiledevice3.usbmux import MuxDevice, list_devices
  ```
- **Signature**:
  ```python
  async def list_devices(usbmux_address: str | None = None) -> list[MuxDevice]
  ```
- **`MuxDevice` properties/methods**:
  - `dev.serial: str` — Device UDID/serial reported by `usbmuxd`
  - `dev.connection_type: str` — `"USB"` or `"Network"`
  - `dev.is_usb: bool` — `True` when `connection_type == "USB"`
  - `dev.matches_udid(udid: str) -> bool` — Compares UDIDs ignoring hyphens

---

### 2. Lockdown Connection (`pymobiledevice3.lockdown`)
- **Import**:
  ```python
  from pymobiledevice3.lockdown import UsbmuxLockdownClient, create_using_usbmux
  ```
- **Signature**:
  ```python
  async def create_using_usbmux(
      serial: str | None = None,
      identifier: str | None = None,
      label: str = "pymobiledevice3",
      autopair: bool = True,
      connection_type: str | None = None,
      pair_timeout: float | None = None,
      local_hostname: str | None = None,
      pair_record: dict[str, Any] | None = None,
      pairing_records_cache_folder: pathlib.Path | None = None,
      port: int = 62078,
      usbmux_address: str | None = None,
  ) -> UsbmuxLockdownClient
  ```
- **Usage & Lifecycle**:
  - Pass `connection_type="USB"` to restrict strictly to USB connections.
  - Pass `serial=udid` to target a specific device when multiple devices are connected.
  - Pass `autopair=True` and `pair_timeout` (e.g. `pair_timeout=0` to detect pending pairing immediately or a finite timeout to wait for user response).
  - `UsbmuxLockdownClient` is an async context manager (`async with await create_using_usbmux(...) as lockdown:`).
- **Key Properties on `LockdownClient`**:
  - `lockdown.all_values.get("DeviceName")` -> User-assigned device name (e.g. `"iPhone"`)
  - `lockdown.display_name` -> Human-readable marketing model (e.g. `"Example iPhone"`)
  - `lockdown.product_type` -> Raw product identifier (e.g. `"EXAMPLE-PRODUCT"`)
  - `lockdown.udid` or `lockdown.identifier` -> Device UDID string
  - `lockdown.product_version` -> iOS version string (e.g. `"18.1"`)
  - `lockdown.product_build_version` -> iOS build version (e.g. `"22B83"`)
  - `lockdown.paired` -> `bool` indicating whether a trusted pair session is active

---

### 3. SpringBoard Icon State (`pymobiledevice3.services.springboard`)
- **Import**:
  ```python
  from pymobiledevice3.services.springboard import SpringBoardServicesService
  ```
- **Service Name**: `"com.apple.springboardservices"`
- **Lifecycle**: Subclass of `LockdownService`; supports `async with SpringBoardServicesService(lockdown) as sb:`.
- **Methods**:
  - `async def get_icon_state(self, format_version: str = "2") -> list[Any]`
    - Sends `{"command": "getIconState", "formatVersion": format_version}` via `send_recv_plist`.
  - `async def set_icon_state(self, newstate: Optional[list[Any]] = None) -> None`
    - Sends `{"command": "setIconState", "iconState": state}` via `send_recv_prefixed(build_plist(...))`.
  - `async def get_homescreen_icon_metrics(self) -> dict[str, float]`
    - Sends `{"command": "getHomeScreenIconMetrics"}`.

---

### 4. Installed Application Discovery (`pymobiledevice3.services.installation_proxy`)
- **Import**:
  ```python
  from pymobiledevice3.services.installation_proxy import InstallationProxyService
  ```
- **Service Name**: `"com.apple.mobile.installation_proxy"`
- **Lifecycle**: Subclass of `LockdownService`; supports `async with InstallationProxyService(lockdown) as iproxy:`.
- **Method**:
  ```python
  async def get_apps(
      self,
      application_type: str = "Any",
      calculate_sizes: bool = False,
      bundle_identifiers: Optional[list[str]] = None,
      show_placeholders: bool = False,
  ) -> dict[str, dict[str, Any]]
  ```
  - Returns a dictionary keyed by bundle identifier (`CFBundleIdentifier`) mapping to each app's info dict.
  - `application_type="Any"` returns both `"User"` and `"System"` apps in a single lookup; each app dict includes `"ApplicationType": "User" | "System"`.

---

### 5. Exceptions (`pymobiledevice3.exceptions`)
- **No device / connection**:
  - `NoDeviceConnectedError`, `DeviceNotFoundError`, `ConnectionFailedToUsbmuxdError`, `ConnectionFailedError`, `MuxException`
- **Pairing / Trust**:
  - `NotPairedError`, `PairingError`, `FatalPairingError`, `InvalidHostIDError`
  - `PairingDialogResponsePendingError` — Trust prompt is waiting on the iPhone screen
  - `UserDeniedPairingError` — User tapped "Don't Trust" on the iPhone
- **Locked / Passcode**:
  - `PasswordRequiredError`, `PasscodeRequiredError` — Device is locked with a passcode when starting a lockdown service or pairing
- **Service / Push Errors**:
  - `StartServiceError`, `LockdownError`, `SetProhibitedError`, `PyMobileDevice3Exception`
