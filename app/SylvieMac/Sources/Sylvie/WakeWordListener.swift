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
    /// Only while Sylvie is speaking or thinking: "stop" / "para" / "arrête" interrupts her.
    var listensForStop = false
    /// True while Sylvie speaks without echo cancellation: she'd hear her own voice (and her name),
    /// so only a strong "stop" counts.
    var deaf = false

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

    private func isCurrent(_ gen: Int) -> Bool {
        lock.lock()
        defer { lock.unlock() }
        return gen == generation
    }

    private func check(_ text: String) {
        if deaf {
            // She's speaking and the mic hears her too: ignore her name, but Lea's "stop" still works.
            if matcher.isStrongStop(text) {
                wakeLog.notice("stop heard while speaking")
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
