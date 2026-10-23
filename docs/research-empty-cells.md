# Empty Home Screen cells: research findings

Research date: 2026-10-22. Research only; no device read or write was performed for this note.

## Conclusion

Native empty space is a real iPhone feature, but its availability in the phone UI does not establish that the USB layout protocol can represent it. Current primary implementation evidence suggests the legacy SpringBoardServices path can reset modern free placement. Adding an `empty` YAML entry would therefore be insufficient without a successful device experiment.

The promising next step is a capability investigation that compares a manually created gap with raw protocol reads, rather than assuming either that gaps are impossible or that a guessed placeholder will work.

## Evidence

### Native layout supports gaps

**Official documentation:** Apple's iPhone guide says apps and widgets can be positioned at the top, bottom, or side of a page. This supports the desired visible outcome through the phone UI; it makes no promise about USB serialization. [Apple: Move apps and widgets](https://support.apple.com/en-ie/guide/iphone/iphd2fc8ce30/26/ios/26).

### The Python and C wrappers transmit opaque layout objects

**Source code:** `pymobiledevice3` defaults `get_icon_state` to `formatVersion = "2"`; a falsy format value omits the key. Its setter passes the supplied list to `setIconState` without translating row/column coordinates. It exposes no slot schema or separate free-placement operation. This is a wrapper limitation, not proof that the service itself has no other format. [pymobiledevice3 SpringBoard source](https://github.com/doronz88/pymobiledevice3/blob/master/pymobiledevice3/services/springboard.py).

**Source code:** libimobiledevice likewise allows a caller-selected read format (or no `formatVersion`) and copies the supplied plist into the write command. The write implementation does not validate modern spatial semantics. A successful send is consequently not evidence that a requested gap survived on screen. [libimobiledevice sbservices.c](https://github.com/libimobiledevice/libimobiledevice/blob/master/src/sbservices.c).

### A comparable current editor reports loss of free placement

**First-party maintainer report:** the author of iOS Home Screen Layout Editor reports that iOS 18+ free placement is unsupported by the SpringBoardServices path used there and that applying changes resets it. The README also reports that only format `"2"` returned the expected legacy page-array structure through the examined Apple private frameworks. Its feature list includes repositioning existing widgets. This is direct evidence from another implementation, but it is not an Apple protocol specification or an exhaustive result for every OS build. [Editor README](https://github.com/dnicolson/iOS-Home-Screen-Layout-Editor).

### An integer-zero placeholder existed in an old read format

**Historical source comment:** Jeff Laing's MobileDeviceAccess header, copyright 2010, describes an unoccupied position as integer `0`, with pages padded to a multiple of four. That concerns an old `getIconState` representation. It does not document modern iOS support for accepting interior zero values through `setIconState` and should not be promoted into a production blank-cell encoding. [MobileDeviceAccess.h, AMSpringboardServices](https://github.com/dannvix/Nimble-Quest-iOS-Trainer/blob/master/MobileDeviceAccess.h#L318-L333).

**Inference:** numeric zero is a more evidence-based experimental candidate than inventing a blank app dictionary. Booleans, numeric zero, empty strings, empty dictionaries, and null must remain distinct during any investigation. Their serializer compatibility and their device meaning are separate questions.

### A version field on writes is another lead, not confirmation

**Source code:** the Rust `idevice` client includes `set_icon_state_with_version`, which adds an optional `formatVersion` to the write request. The implementation merely sends the plist; it supplies neither an accepted-version table nor tests proving free placement. This justifies investigating versioned requests, but not claiming that a new format solves the issue. [idevice SpringBoardServices source](https://docs.rs/idevice/latest/src/idevice/services/springboardservices.rs.html).

### Managed configuration is not an established alternative for blanks or widgets

**Official schema:** Apple's current Home Screen Layout payload requires supervised iOS devices and defines only `Application`, `Folder`, and `WebClip` item types. It contains no blank, widget, or row/column field. Apple also states that this payload locks the layout against user modification. Referencing a WebClip does not create it. Therefore the documented profile schema does not provide the requested capabilities and would introduce a different management model. This is a conclusion about the published schema, not a claim about all undocumented behavior. [Apple device-management Home Screen Layout schema](https://github.com/apple/device-management/blob/release/mdm/profiles/com.apple.homescreenlayout.yaml).

## Proposed experiments and implementation boundary

These are proposed tests, not completed tests and not authorization to change a phone.

1. On a test device, record OS build, model, icon-size setting, orientation, and metrics. Capture a screenshot and raw state before and after manually moving one app to create an interior gap. Use equivalent visible pages, including any hidden Focus pages.
2. Compare read requests with format `"2"`, omitted format, and only other values justified by source or a documented experiment. Preserve the raw response and its types. Do not run a write while probing reads.
3. If a read format exposes position metadata or explicit blanks, build synthetic fixture coverage before proposing a write. If the compact representation stays identical before and after the visible move, that read path cannot verify the spatial requirement.
4. Only on a separately authorized test device, test an unchanged-state round trip first. Then test one gap using an observed encoding, or a clearly labeled historical-zero hypothesis. Back up before every write; compare raw readback and a screenshot afterward. Stop on a mismatch rather than trying many guessed encodings on a personal layout.
5. Verify across a SpringBoard reload or reboot, and repeat on each supported OS/model family before advertising support. A transport success or equality of compact arrays cannot prove pixel-position success when the protocol omits positions.

If support is demonstrated, add a capability-gated spatial model (`row`, `column`, `width`, `height`, explicit empty slots), overlap checks, and a grid preview that matches the observed protocol. Retain the legacy ordered-list backend. Until then, reject unsupported spatial plans before applying them and label the manual portion of the preview. Adding native widgets through UI automation is a separate backend investigation; MDM's published layout schema does not establish widget creation.
