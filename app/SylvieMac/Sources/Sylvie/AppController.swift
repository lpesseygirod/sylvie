import AppKit
import AVFoundation
import ServiceManagement
import Speech
import SylvieCore

enum VoiceState: String {
    case starting, idle, listening, thinking, speaking, muted, offline

    var symbol: String {
        switch self {
        case .starting: "hourglass"
        case .idle: "waveform.circle"
        case .listening: "ear.fill"
        case .thinking: "ellipsis.circle.fill"
        case .speaking: "speaker.wave.2.fill"
        case .muted: "mic.slash"
        case .offline: "exclamationmark.triangle"
        }
    }

    var label: String {
        switch self {
        case .starting: "Starting…"
        case .idle: "Say “Sylvie” or press ⌥Space"
        case .listening: "Listening…"
        case .thinking: "Thinking…"
        case .speaking: "Speaking…"
        case .muted: "Microphone muted"
        case .offline: "Sylvie's server isn't running"
        }
    }

    /// The orb's look for this state (menu bar glyph, panel, voice mode).
    var orb: OrbState {
        switch self {
        case .starting, .idle: .idle
        case .listening: .listening
        case .thinking: .thinking
        case .speaking: .speaking
        case .muted, .offline: .muted
        }
    }
}

/// Wake word → listen → send audio → speak the answer, with the floating panel showing it all.
@MainActor
final class AppController: ObservableObject {
    @Published var state: VoiceState = .starting {
        didSet { if state != oldValue { stateChanged(from: oldValue) } }
    }
    @Published var detail = "" {
        didSet { if detail != oldValue { forwardState() } }
    }
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled
    @Published var learningPaused = false
    @Published private(set) var menuIcon = AppController.glyph(.idle, time: 0)

    private let port = 8765
    private var api: APIClient?
    private let pipeline = AudioPipeline()
    private let wake = WakeWordListener()
    private let vad = VoiceActivityDetector()
    private lazy var speaker = Speaker(pipeline: pipeline)
    private var panel: PanelController?
    private var chat: ChatWindowController?
    private var hotKey: HotKey?

    private var preRoll = RingBuffer(capacity: 16000 * 3 / 2)  // 1.5 s before the wake word
    private var recording: [Float] = []
    private var recordingActive = false
    private var heardSpeech = false
    private var turnTask: Task<Void, Never>?
    private var conversationId: String?
    private var lastTurnAt = Date.distantPast
    private var lastLanguage = "en"
    private let conversationTimeout: TimeInterval = 5 * 60  // after 5 min of quiet, a new conversation
    private var serverWasReady = false
    private var voiceModeActive = false  // the chat window is showing voice mode (/voice)
    private var glyphTimer: Timer?
    private let glyphStart = Date()
    private var micLevel = 0.0
    private var lastLevelSent = Date.distantPast
    private var autoHide: DispatchWorkItem?
    private var ignoreMicUntil = Date.distantPast

    func start() {
        hotKey = HotKey { [weak self] in self?.hotKeyPressed() }
        Task { await connect() }
        // Keep listening for "Sylvie" at all times: picks up permissions granted after launch and a
        // listener that stopped. (Measured 2026-11-06: the app was opened before Lea allowed Speech
        // Recognition, so the wake word never started until the app was restarted.)
        Timer.scheduledTimer(withTimeInterval: 10, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.checkListening() }
        }
    }

    private func checkListening() {
        guard serverWasReady, state != .muted, state != .starting else { return }
        if !wake.isEnabled {
            wakeLog.notice("check: not listening (speech auth \(SFSpeechRecognizer.authorizationStatus().rawValue, privacy: .public), available \(self.wake.isAvailable, privacy: .public), state \(String(describing: self.state), privacy: .public))")
        }
        if !pipeline.running {
            Task { await startAudio() }
            return
        }
        if !wake.isEnabled, SFSpeechRecognizer.authorizationStatus() == .authorized, wake.isAvailable {
            wake.start()
            detail = ""
        }
    }

    // MARK: Setup

    private func connect() async {
        guard let token = Keychain.token() else {
            state = .offline
            detail = "No token yet: run `make run` in the Sylvie folder once."
            retryConnectLater()
            return
        }
        let api = APIClient(token: token, port: port)
        self.api = api
        speaker.api = api
        guard await api.isReady() else {
            state = .offline
            detail = "Start it with `make run` (or install the background service)."
            retryConnectLater()
            return
        }
        if panel == nil {
            WebSetup.setSessionCookie(token: token, port: port) {}
            let panel = PanelController(base: api.base)
            panel.onOpenChat = { [weak self] cid in self?.openChat(cid) }
            self.panel = panel
            let chat = ChatWindowController(base: api.base)
            chat.onMessage = { [weak self] msg in self?.chatMessage(msg) }
            self.chat = chat
        }
        if let info = await api.info() { learningPaused = info["learning_paused"] as? Bool ?? false }
        panel?.load()
        serverWasReady = true
        await startAudio()
    }

    private func retryConnectLater() {
        Task {
            try? await Task.sleep(for: .seconds(5))
            await connect()
        }
    }

    private func startAudio() async {
        guard state != .muted else { return }
        let micOK = await AVCaptureDevice.requestAccess(for: .audio)
        let speechOK = await withCheckedContinuation { cont in
            SFSpeechRecognizer.requestAuthorization { cont.resume(returning: $0 == .authorized) }
        }
        guard micOK else {
            state = .muted
            detail = "Allow the microphone in System Settings → Privacy & Security → Microphone."
            return
        }
        pipeline.onSamples = { [weak self] samples in
            DispatchQueue.main.async { self?.handle(samples) }
        }
        pipeline.onBuffer = { [weak self] buffer in self?.wake.append(buffer) }
        wake.onWake = { [weak self] in self?.wakeWordHeard() }
        wake.onStopCommand = { [weak self] in self?.interrupt() }
        do {
            try pipeline.start()
        } catch {
            state = .offline
            detail = "Couldn't start the microphone: \(error.localizedDescription)"
            return
        }
        wakeLog.notice("audio started: mic \(micOK, privacy: .public), speech \(speechOK, privacy: .public), on-device \(self.wake.isAvailable, privacy: .public), echo cancellation \(self.pipeline.echoCancelled, privacy: .public)")
        if speechOK && wake.isAvailable {
            wake.start()
            detail = ""
        } else {
            detail = "Wake word off (allow Speech Recognition, and on-device dictation). ⌥Space still works."
        }
        state = .idle
    }

    // MARK: Voice flow

    private func wakeWordHeard() {
        switch state {
        case .idle:
            beginListening(chime: true)
        case .speaking, .thinking:
            interrupt()
            beginListening(chime: true)
        default:
            break
        }
    }

    private func hotKeyPressed() {
        if voiceModeActive, chat?.isVisible == true {
            orbPressed()
            return
        }
        guard let panel else { return }
        if panel.isVisible && state == .idle {
            panel.hide()
            return
        }
        panel.show()
        panel.focusForTyping()
        if state == .idle { beginListening(chime: true) }
    }

    private func beginListening(chime: Bool, greet: Bool = true) {
        guard pipeline.running else { return }
        if chime { NSSound(named: "Tink")?.play() }
        ignoreMicUntil = Date().addingTimeInterval(pipeline.echoCancelled ? 0 : 0.35)  // not the chime
        if Date().timeIntervalSince(lastTurnAt) > conversationTimeout && !voiceModeActive { conversationId = nil }
        if !voiceModeActive { panel?.show() }
        recording = preRoll.snapshot()
        vad.reset()
        heardSpeech = false
        recordingActive = true
        state = .listening
        wake.listensForStop = false
        // Greet only if Lea paused after "Sylvie" — if she's already talking, don't interrupt her.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { [weak self] in
            guard greet, let self, self.recordingActive, !self.heardSpeech else { return }
            let greetings = ["en": "Hello Lea", "es": "Hola Lea", "fr": "Bonjour Lea"]
            self.speaker.say(greetings[self.lastLanguage] ?? "Hello Lea", language: self.lastLanguage)
        }
    }

    private func handle(_ samples: [Float]) {
        preRoll.append(samples)
        guard recordingActive else { return }
        // Without echo cancellation the mic hears Sylvie herself (her "Hello Lea", the chime): measured
        // 2026-11-06, her greeting ended up in Lea's question ("Sylvie. Hello Lea."). Leave those out.
        if !pipeline.echoCancelled && (speaker.isSpeaking || Date() < ignoreMicUntil) { return }
        recording.append(contentsOf: samples)
        sendLevel(samples)
        for event in vad.process(samples) {
            switch event {
            case .speechStarted:
                heardSpeech = true
            case .speechEnded, .maxLengthReached:
                finishRecording()
            case .noSpeechTimeout:
                recordingActive = false
                state = .idle
            }
        }
    }

    private func finishRecording() {
        recordingActive = false
        let wav = WAV.encode(samples: recording)
        recording.removeAll()  // audio is dropped as soon as it's packaged
        state = .thinking
        wake.listensForStop = true
        runTurn(wav: wav)
    }

    private func runTurn(wav: Data) {
        guard let api else { return }
        turnTask?.cancel()
        turnTask = Task { [weak self] in
            guard let self else { return }
            do {
                for try await event in api.voiceTurn(wav: wav, conversationId: self.conversationId) {
                    if Task.isCancelled { break }
                    if self.voiceModeActive { self.chat?.voiceEvent(event) } else { self.panel?.send(event: event) }
                    self.apply(event)
                }
            } catch {
                if !Task.isCancelled { self.detail = "Lost the connection to the server." }
            }
            if self.state == .thinking { self.state = .idle }
            self.lastTurnAt = Date()
        }
    }

    private func apply(_ event: [String: Any]) {
        switch event["type"] as? String {
        case "meta":
            conversationId = event["conversation_id"] as? String
        case "transcript":
            if let lang = event["language"] as? String { lastLanguage = lang }
            if (event["text"] as? String ?? "").isEmpty { state = .idle }
        case "speak":
            guard let text = event["text"] as? String else { return }
            if state != .speaking { wake.markStopsSeen() }  // only a "stop" said from now on counts
            state = .speaking
            wake.deaf = !pipeline.echoCancelled
            speaker.onFinished = { [weak self] in
                guard let self, self.state == .speaking else { return }
                self.state = .idle
                self.wake.listensForStop = false
                self.wake.deaf = false
                self.wake.restart()  // fresh transcript: what she said herself doesn't count
            }
            speaker.say(text, language: event["language"] as? String ?? lastLanguage)
        case "done":
            if !speaker.isSpeaking && state == .thinking { state = .idle }
        case "error":
            detail = event["text"] as? String ?? "Something went wrong."
        default:
            break
        }
    }

    /// "Sylvie, stop", a tap on the menu, or the wake word while she's talking.
    func interrupt() {
        speaker.stop()
        turnTask?.cancel()
        turnTask = nil
        if let cid = conversationId { api?.stop(conversationId: cid) }
        recordingActive = false
        wake.listensForStop = false
        wake.deaf = false
        state = pipeline.running ? .idle : .muted
    }

    // MARK: Menu actions

    func toggleMute() {
        if state == .muted {
            state = .starting
            Task { await startAudio() }
        } else {
            interrupt()
            wake.stop()
            pipeline.stop()
            state = .muted
            detail = ""
        }
    }

    func openChat(_ cid: String? = nil) {
        chat?.open(conversationId: cid ?? conversationId)
    }

    func openVoiceMode() {
        var path = "/voice"
        if let cid = conversationId, Date().timeIntervalSince(lastTurnAt) < conversationTimeout { path += "?c=\(cid)" }
        panel?.hide()
        chat?.open(path: path)
    }

    func openDashboard() {
        chat?.open(path: "/dashboard")
    }

    func toggleLearningPause() {
        guard let api else { return }
        let want = !learningPaused
        Task {
            if let paused = await api.pauseLearning(want) { learningPaused = paused }
        }
    }

    // MARK: Voice mode (the chat window's /voice page)

    private func chatMessage(_ msg: [String: Any]) {
        switch msg["type"] as? String {
        case "voiceMode":
            let active = msg["active"] as? Bool ?? false
            guard active != voiceModeActive || active else { return }
            voiceModeActive = active
            guard active else { return }
            if let cid = msg["conversation_id"] as? String {
                conversationId = cid
                lastTurnAt = Date()
            }
            panel?.hide()
            forwardState()
            if state == .idle { beginListening(chime: true, greet: false) }  // opening voice mode = "I want to talk"
        case "voiceListen":
            orbPressed()
        case "voiceStop":
            interrupt()
        case "voiceMute":
            toggleMute()
        default:
            break
        }
    }

    /// A tap on the orb (or ⌥Space in voice mode): talk, finish talking, or interrupt Sylvie.
    private func orbPressed() {
        switch state {
        case .idle:
            beginListening(chime: true, greet: false)
        case .listening:
            if heardSpeech { finishRecording() } else { recordingActive = false; state = .idle }
        case .thinking, .speaking:
            interrupt()
            beginListening(chime: true, greet: false)
        default:
            break
        }
    }

    // MARK: Orb everywhere: menu bar glyph, panel, voice mode

    private func stateChanged(from old: VoiceState) {
        forwardState()
        updateGlyphTimer()
        menuIcon = Self.glyph(state.orb, time: Date().timeIntervalSince(glyphStart))
        // Close the panel a few seconds after the answer, unless Lea is typing there or a "Send?" card waits.
        autoHide?.cancel()
        if state == .idle, old == .speaking || old == .thinking, let panel, panel.isVisible {
            let work = DispatchWorkItem { [weak self] in
                guard let self, self.state == .idle, let panel = self.panel,
                      !panel.isKey, !panel.hasPendingConfirmation else { return }
                panel.hide()
            }
            autoHide = work
            DispatchQueue.main.asyncAfter(deadline: .now() + 6, execute: work)
        }
    }

    private func forwardState() {
        let web = state == .offline ? "offline" : state.orb.rawValue
        let note = state == .offline || state == .muted ? detail : ""
        panel?.voiceState(web, detail: note)
        if voiceModeActive { chat?.voiceState(web, detail: note) }
    }

    private func sendLevel(_ samples: [Float]) {
        var sum: Float = 0
        for x in samples { sum += x * x }
        micLevel = Double(min(1, sqrt(sum / Float(max(samples.count, 1))) * 12))
        guard Date().timeIntervalSince(lastLevelSent) > 0.05 else { return }
        lastLevelSent = Date()
        if voiceModeActive { chat?.voiceLevel(micLevel) } else { panel?.voiceLevel(micLevel) }
    }

    private func updateGlyphTimer() {
        if state.orb.animates {
            guard glyphTimer == nil else { return }
            let timer = Timer(timeInterval: 1.0 / 12, repeats: true) { [weak self] _ in
                MainActor.assumeIsolated {
                    guard let self else { return }
                    let level = self.state == .listening ? self.micLevel : 0
                    self.menuIcon = Self.glyph(self.state.orb, time: Date().timeIntervalSince(self.glyphStart), level: level)
                }
            }
            RunLoop.main.add(timer, forMode: .common)  // keeps animating while the menu is open
            glyphTimer = timer
        } else {
            glyphTimer?.invalidate()
            glyphTimer = nil
        }
    }

    static func glyph(_ state: OrbState, time: Double, level: Double = 0) -> NSImage {
        guard let cg = OrbGlyph.render(state: state, time: time, pixels: 36, level: level) else {
            return NSImage(systemSymbolName: "circle.fill", accessibilityDescription: "Sylvie") ?? NSImage()
        }
        return NSImage(cgImage: cg, size: NSSize(width: 18, height: 18))
    }

    func showPanel() {
        panel?.show()
        panel?.focusForTyping()
    }

    func setLaunchAtLogin(_ on: Bool) {
        do {
            if on { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
        } catch {
            detail = "Couldn't change Launch at Login: \(error.localizedDescription)"
        }
        launchAtLogin = SMAppService.mainApp.status == .enabled
    }
}
