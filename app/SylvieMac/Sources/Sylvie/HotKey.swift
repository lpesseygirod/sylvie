import Carbon.HIToolbox

/// A global keyboard shortcut (default ⌥Space) to open Sylvie without the wake word.
/// Uses the Carbon hot-key API, which doesn't need Accessibility permission.
final class HotKey {
    private static var action: (() -> Void)?
    private var ref: EventHotKeyRef?

    init(keyCode: UInt32 = UInt32(kVK_Space), modifiers: UInt32 = UInt32(optionKey), action: @escaping () -> Void) {
        HotKey.action = action
        var spec = EventTypeSpec(eventClass: OSType(kEventClassKeyboard), eventKind: UInt32(kEventHotKeyPressed))
        InstallEventHandler(GetApplicationEventTarget(), { _, _, _ in
            DispatchQueue.main.async { HotKey.action?() }
            return noErr
        }, 1, &spec, nil, nil)
        let id = EventHotKeyID(signature: OSType(0x5359_4C56), id: 1)  // "SYLV"
        RegisterEventHotKey(keyCode, modifiers, id, GetApplicationEventTarget(), 0, &ref)
    }

    deinit {
        if let ref { UnregisterEventHotKey(ref) }
    }
}
