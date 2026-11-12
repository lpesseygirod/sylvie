import AppKit

/// Turns Spotify down while Sylvie listens or speaks, and back up afterwards.
///
/// Lea's Mac can't do echo cancellation, so with music playing the microphone hears the song as much
/// as her ("while it's putting music I still need to be able to call her"). Only
/// touches Spotify when it's already running and playing; never starts it.
final class MusicDucker {
    private let quietVolume = 12
    private var savedVolume: Int?
    private let queue = DispatchQueue(label: "sylvie.duck")

    func duck() {
        queue.async { [self] in
            guard savedVolume == nil, spotifyRunning() else { return }
            let script = """
            tell application id "com.spotify.client"
                if player state is playing then
                    set v to sound volume
                    if v > \(quietVolume) then set sound volume to \(quietVolume)
                    return v
                end if
                return -1
            end tell
            """
            if let v = run(script), v >= 0 { savedVolume = v }
        }
    }

    func restore() {
        queue.async { [self] in
            guard let v = savedVolume else { return }
            savedVolume = nil
            guard spotifyRunning() else { return }
            _ = run("tell application id \"com.spotify.client\" to set sound volume to \(v)")
        }
    }

    private func spotifyRunning() -> Bool {
        !NSRunningApplication.runningApplications(withBundleIdentifier: "com.spotify.client").isEmpty
    }

    private func run(_ source: String) -> Int? {
        var error: NSDictionary?
        let result = NSAppleScript(source: source)?.executeAndReturnError(&error)
        if let error { wakeLog.notice("Spotify volume: \(String(describing: error[NSAppleScript.errorNumber]), privacy: .public)") }
        return result.map { Int($0.int32Value) }
    }
}
