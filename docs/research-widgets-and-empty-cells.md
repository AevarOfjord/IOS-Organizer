# Empty cells and native widget creation: feasibility and implementation plan

Research date: October 22, 2026. This investigation used online primary sources and read-only inspection of the installed dependency. It did not access or change a phone, enable developer features, install software, or test UI automation.

## Recommendation

Add an optional UI automation backend alongside the existing SpringBoard USB layout backend. Investigate CoreDevice touch input in the already-installed `pymobiledevice3` first. Use the normal Home Screen interface to position apps and create actual native widgets. Keep bulk folder/app changes in the existing backend, and perform spatial UI work afterward.

This is a feasible prototype direction supported by available input/capture primitives, not a demonstrated end-to-end feature. Both blank placement and adding the requested Weather, Calendar, and Reminders widgets still need real-device validation. Adding `empty` entries to the compiler alone is not an established solution.

## Findings and confidence

| Capability | Evidence | What is still unproven |
| --- | --- | --- |
| Apps at the bottom with empty rows above | Apple documents native free placement | Whether a USB state format can serialize and restore those positions |
| Existing widgets moved by USB | Current project preserves opaque objects; comparable editor supports repositioning | Creation of valid new widgets from a fabricated payload |
| Native touch input plus screen capture | Upstream CoreDevice source/docs; corresponding modules present in the pinned local dependency | Reliable SpringBoard editing, gallery navigation, and placement on each supported OS/device |
| Native widget creation through the gallery | Apple documents the user workflow | Reliable automated execution of that workflow |
| Public WidgetKit API placing arbitrary widgets | No such API found in reviewed documentation | Do not advertise this capability |

### 1. Empty space is supported by iOS itself

Apple explicitly describes moving apps and widgets to the top, bottom, or side of the Home Screen. The visual outcome does not require fake blank icons. [Apple: Move apps and widgets](https://support.apple.com/en-ie/guide/iphone/iphd2fc8ce30/27/ios/27).

The comparable iOS Home Screen Layout Editor reports that its SpringBoardServices implementation loses iOS 18+ free placement when applying a layout. This is evidence of a practical protocol limitation, not an exhaustive Apple specification. [Maintainer's README](https://github.com/dnicolson/iOS-Home-Screen-Layout-Editor).

See [the focused empty-cell research](research-empty-cells.md) for historical zero-valued slots, format-version leads, MDM limitations, and controlled experiments. None establishes a working modern sparse-layout encoding.

### 2. Our existing dependency includes a promising second interface

Upstream `pymobiledevice3` documents CoreDevice screenshot, tap, drag, typing, and screen-stream commands. Its recipes require an RSD developer-services connection and a mounted Developer Disk Image. These operations are separate from `setIconState`. [Upstream CLI recipes](https://doronz88.github.io/pymobiledevice3/guides/cli-recipes/).

The HID implementation sends real contact/release reports. Its `touch_session` helper establishes the active media stream required for touch delivery; simply sending isolated reports is insufficient. The CLI drag helper interpolates a contact path and releases at the destination. [HID implementation](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/remote/core_device/hid_service.py), [CoreDevice CLI implementation](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/cli/developer/core_device.py).

Local source inspection confirmed these modules exist in the project's pinned `pymobiledevice3==11.41.2`: `remote/core_device/hid_service.py`, `remote/core_device/screen_stream.py`, `cli/developer/core_device.py`, and `cli/developer/wda.py`. This proves code availability, not that this phone is configured for developer services or that the proposed workflow succeeds.

**Engineering inference:** screenshot-guided contact, hold, drag, and release can be used to prototype the same Home Screen interactions a person performs. A long press must deliberately hold contact before moving or releasing; the default drag helper does not supply an icon-pickup hold. Do not infer gesture success from a successful network send.

### 3. Widget creation should go through the actual gallery

Apple's documented flow enters Home Screen editing, opens Add Widget, chooses the app and size, and adds the widget. The guide also describes converting eligible app icons into widgets and editing a widget's options. Using the gallery should let iOS create the real widget identity and configuration instead of inventing opaque state. [Apple: Add, edit, and remove widgets](https://support.apple.com/en-lamr/guide/iphone/iphb8f1bf206/ios).

The reviewed `WidgetCenter` interface enumerates configured widgets and refreshes timelines/recommendations; it does not document a way to install arbitrary other apps' widgets on the Home Screen. A companion WidgetKit app therefore does not by itself solve native Weather/Calendar/Reminders placement. This conclusion is limited to the reviewed public API. [Apple: WidgetCenter](https://developer.apple.com/documentation/widgetkit/widgetcenter).

### 4. Alternative UI backends

**iPhone Mirroring:** Apple documents Home Screen editing using click-and-hold from a Mac. It needs compatible devices, the same Apple Account, and other Continuity prerequisites. Agent control of its UI requires separate app-access approval. If access is denied, retain guided manual steps and honor that decision. [Apple: iPhone Mirroring](https://support.apple.com/en-us/120421).

**Appium / WebDriverAgent:** the XCUITest driver exposes touch-and-hold and drag methods. Real-device setup includes a provisioned WDA runner, trust, Developer Mode, and UI Automation. This may help with element targeting, but is a larger onboarding burden for novice users. It remains an alternative to prototype, not an established widget-specific API. [Appium gesture methods](https://appium.github.io/appium-xcuitest-driver/latest/reference/execute-methods/), [Appium device preparation](https://appium.github.io/appium-xcuitest-driver/latest/getting-started/device-setup/).

## Proposed project changes

These are recommendations, not changes implemented by this research.

1. **Represent spatial intent explicitly.** Add a versioned design model with row/column positions, item spans, and requested native widgets. Empty cells represent unoccupied space, not fake apps. Keep an approved design distinct from a pushable legacy layout.
2. **Detect capabilities before preview and apply.** Track bulk layout access, screen observation, touch input, sparse placement, widget creation, and configuration separately. Report unknown versus tested support. Block an exact-layout apply when its requirements are unmet; do not silently compact the design or call a partial result complete.
3. **Isolate the optional UI backend.** Keep device connections and input execution in the device layer, with planning/validation independent of a phone. Provide bounded screenshot-guided actions, cancellation, and clear operation results. Local permission to use a service does not prove that it works on every phone.
4. **Apply bulk changes before spatial changes.** Build and verify folders/app ordering first, then create and position native widgets and move icons into their exact cells. Avoid a subsequent legacy `setIconState` write that might undo the final sparse arrangement.
5. **Verify the visible result.** Compare app identities and opaque objects using existing checks, and compare actual screen positions using screenshots or reliable UI bounds. Compact array equality cannot prove that gaps survived. Check widget identity, selected size, and requested list/location settings separately.
6. **Make recovery claims accurate.** Keep raw backups, screenshots, and the spatial plan. If the legacy read format omits gaps, a raw backup alone may not restore them. Widget configuration or creation may also need UI steps; advertise full undo only after testing it.
7. **Keep onboarding guided.** The agent runs diagnostics; the user handles device unlock/trust and any required on-device developer setup. If UI access is unavailable, show the exact remaining manual steps and preserve the approved preview. Never ask users to remove their passcode to simplify setup.

## Smallest useful prototype

Run this only with explicit device-testing authorization. This research request does not authorize installing WDA, enabling Developer Mode, mounting a developer image, or changing the Home Screen.

- Check developer-service prerequisites and capture a current screen without changing it.
- On a designated test page, demonstrate one app moving to a lower cell while leaving a real gap above. Reobserve after leaving edit mode.
- Add one real small Weather widget through the gallery, then read its actual opaque object and confirm its visual size and location.
- Extend to Calendar and medium Reminders, including list selection, then the approved eight-app rows.
- Preserve every pre-existing app and widget, exercise cancellation, and validate persistence after returning to the page. Test reconnect and OS/model differences before release.

**Go/no-go:** proceed if the UI backend can observe and reliably complete those actions. If it cannot, retain the current bulk organizer plus guided manual widget setup, with unsupported exact positions clearly marked before apply. No research finding here justifies promising automatic widget creation today.
