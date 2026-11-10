import AVFoundation
import os
import Speech
import SylvieCore

/// Diagnostics only (Console.app, subsystem app.sylvie.mac): states and error codes, never what Lea says.
let wakeLog = Logger(subsystem: "app.sylvie.mac", category: "wake")

/// Listens for "Sylvie" using Apple's on-device speech recognizer (nothing leaves the Mac).
///
/// Interim solution until the custom wake-word model is trained (plan, Phase 2). Recognition
/// tasks are restarted regularly because they're meant for utterances, not endless audio.
final class WakeWordListener {
    var onWake: (() -> Void)?
    var onStopCommand: (() -> Void)?
    /// "stop listening" / "deja de escuchar" / "arrête d'écouter": stop completely (mic off).
    var onStopListening: (() -> Void)?
    private var stopHeardAt = Date.distantPast
    /// Only while Sylvie is speaking or thinking: "stop" / "para" / "arrête" interrupts her.
    var listensForStop = false
    /// True while Sylvie speaks without echo cancellation: she'd hear her own voice (and her name),
    /// so only a strong "stop" counts, or her name said while someone is talking over her.
    var deaf = false
    /// Until when someone was last heard talking over her (BargeInDetector, via leaTalkingOverVoice()).
    private var overVoiceUntil = Date.distantPast
    /// Her name appeared in the transcript just before the talking-over was noticed.
    private var pendingWakeAt = Date.distantPast

    private let recognizer: SFSpeechRecognizer?
    private let matcher = WakeMatcher()
    private let lock = NSLock()
    private var request: SFSpeechAudioBufferRecognitionRequest?
    private var task: SFSpeechRecognitionTask?
    /// Bumped for every new recognition task. Callbacks from older (cancelled) tasks are ignored:
    /// measured 2026-11-06, a cancelled task's error triggered another restart, which cancelled the
    /// new task, and so on every ~0.5 s, so the listener never ran long enough to hear "Sylvie".
    private var generation = 0
    private var seenWakes = 0
    private var seenStops = 0
    private var startedAt = Date()
    private var enabled = false
    private let maxTaskSeconds: TimeInterval = 45

    init(localeIdentifier: String = "en-US") {
        recognizer = SFSpeechRecognizer(locale: Locale(identifier: localeIdentifier))
    }

    var isAvailable: Bool { recognizer?.isAvailable == true && recognizer?.supportsOnDeviceRecognition == true }

    /// True while it's meant to be listening (between start() and stop()).
    var isEnabled: Bool {
        lock.lock()
        defer { lock.unlock() }
        return enabled
    }

    func start() {
        wakeLog.notice("wake listener started (on-device available: \(self.isAvailable, privacy: .public))")
        lock.lock()
        enabled = true
        lock.unlock()
        beginTask()
    }

    func stop() {
        lock.lock()
        enabled = false
        task?.cancel()
        request?.endAudio()
        task = nil
        request = nil
        lock.unlock()
    }

    func append(_ buffer: AVAudioPCMBuffer) {
        lock.lock()
        let req = request
        let tooOld = Date().timeIntervalSince(startedAt) > maxTaskSeconds
        lock.unlock()
        req?.append(buffer)
        if tooOld { restart() }
    }

    func restart() {
        lock.lock()
        task?.cancel()
        request?.endAudio()
        task = nil
        request = nil
        let on = enabled
        lock.unlock()
        if on { beginTask() }
    }

    private func beginTask() {
        guard let recognizer, recognizer.isAvailable else {
            wakeLog.notice("recognizer not available yet; retrying in 2 s")
            // Not ready yet (e.g. right after login): try again shortly instead of going deaf for good.
            DispatchQueue.global().asyncAfter(deadline: .now() + 2) { [weak self] in self?.restart() }
            return
        }
        let req = SFSpeechAudioBufferRecognitionRequest()
        req.shouldReportPartialResults = true
        req.requiresOnDeviceRecognition = true  // private: never sent to Apple's servers
        req.contextualStrings = ["Sylvie"]
        req.addsPunctuation = false
        lock.lock()
        request = req
        seenWakes = 0
        seenStops = 0
        resetStopsOnNext = false  // a fresh transcript: every stop word in it is new
        startedAt = Date()
        generation += 1
        let gen = generation
        lock.unlock()
        let newTask = recognizer.recognitionTask(with: req) { [weak self] result, error in
            guard let self, self.isCurrent(gen) else { return }  // an old task we already replaced
            if let result {
                let text = result.bestTranscription.formattedString
                self.check(text)
                if result.isFinal { self.restart() }
            } else if let error {
                let ns = error as NSError
                wakeLog.notice("recognition task ended: \(ns.domain, privacy: .public) \(ns.code, privacy: .public)")
                DispatchQueue.global().asyncAfter(deadline: .now() + 1) { [weak self] in
                    guard let self, self.isCurrent(gen) else { return }
                    self.restart()
                }
            }
        }
        lock.lock()
        task = newTask
        lock.unlock()
    }

    /// Called when Sylvie starts speaking: stop words already in the transcript (e.g. in Lea's
    /// question) don't count, only new ones.
    func markStopsSeen() {
        lock.lock()
        resetStopsOnNext = true  // the next partial result sets the baseline
        lock.unlock()
    }

    private var resetStopsOnNext = false

    /// The app heard someone talking over Sylvie (and turned her down): for the next 2 s, her name in the
    /// transcript is Lea's, not Sylvie's own voice. A name heard just before also counts (the recognizer
    /// is sometimes faster than the level check).
    func leaTalkingOverVoice() {
        lock.lock()
        overVoiceUntil = Date().addingTimeInterval(2)
        let fire = Date().timeIntervalSince(pendingWakeAt) < 1.5
        if fire { pendingWakeAt = .distantPast }
        lock.unlock()
        if fire {
            wakeLog.notice("her name heard over her voice")
            DispatchQueue.main.async { self.onWake?() }
            restart()
        }
    }

    private func isCurrent(_ gen: Int) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return gen == generation
    }

    private func check(_ text: String) {
        lock.lock()
        let recentStop = Date().timeIntervalSince(stopHeardAt) < 2.5
        lock.unlock()
        if matcher.isStopListening(text) || (recentStop && matcher.isListenTail(text)) {
            wakeLog.notice("stop listening heard")
            lock.lock()
            stopHeardAt = .distantPast
            lock.unlock()
            DispatchQueue.main.async { self.onStopListening?() }
            restart()
            return
        }
        if deaf {
            // She's speaking and the mic hears her too: her name only counts while someone is heard
            // talking over her (she may say it herself), but Lea's "stop" always works.
            let stops = matcher.strongStopCount(in: text)
            let wakes = matcher.wakeCount(in: text)
            lock.lock()
            if resetStopsOnNext {
                seenStops = stops
                seenWakes = wakes
                resetStopsOnNext = false
            }
            let newStop = stops > seenStops
            let newWake = wakes > seenWakes
            seenStops = max(seenStops, stops)
            seenWakes = max(seenWakes, wakes)
            let overVoice = Date() < overVoiceUntil
            if newWake && !overVoice { pendingWakeAt = Date() }
            lock.unlock()
            if newWake && overVoice && !newStop {
                wakeLog.notice("her name heard over her voice")
                DispatchQueue.main.async { self.onWake?() }
                restart()
                return
            }
            if newStop {
                wakeLog.notice("stop heard while speaking")
                lock.lock()
                stopHeardAt = Date()
                lock.unlock()
                DispatchQueue.main.async { self.onStopCommand?() }
                restart()
            }
            return
        }
        let count = matcher.wakeCount(in: text)
        lock.lock()
        let isNewWake = count > seenWakes
        if isNewWake { seenWakes = count }
        let stopMode = listensForStop
        lock.unlock()
        if stopMode && matcher.isStop(text) {
            wakeLog.notice("stop heard")
            lock.lock()
            stopHeardAt = Date()
            lock.unlock()
            DispatchQueue.main.async { self.onStopCommand?() }
            restart()
            return
        }
        if isNewWake {
            wakeLog.notice("wake word heard")
            DispatchQueue.main.async { self.onWake?() }
            restart()  // fresh transcript, so the next "Sylvie" counts again
        }
    }
}
