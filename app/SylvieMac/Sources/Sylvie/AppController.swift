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
}

/// Wake word → listen → send audio → speak the answer, with the floating panel showing it all.
@MainActor
final class AppController: ObservableObject {
    @Published var state: VoiceState = .starting
    @Published var detail = ""
    @Published var launchAtLogin = SMAppService.mainApp.status == .enabled

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

    func start() {
        hotKey = HotKey { [weak self] in self?.hotKeyPressed() }
        Task { await connect() }
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
            chat = ChatWindowController(base: api.base)
        }
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
        guard let panel else { return }
        if panel.isVisible && state == .idle {
            panel.hide()
            return
        }
        panel.show()
        panel.focusForTyping()
        if state == .idle { beginListening(chime: true) }
    }

    private func beginListening(chime: Bool) {
        guard pipeline.running else { return }
        if chime { NSSound(named: "Tink")?.play() }
        if Date().timeIntervalSince(lastTurnAt) > conversationTimeout { conversationId = nil }
        panel?.show()
        recording = preRoll.snapshot()
        vad.reset()
        heardSpeech = false
        recordingActive = true
        state = .listening
        wake.listensForStop = false
        // Greet only if Lea paused after "Sylvie" — if she's already talking, don't interrupt her.
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.9) { [weak self] in
            guard let self, self.recordingActive, !self.heardSpeech else { return }
            let greetings = ["en": "Hello Lea", "es": "Hola Lea", "fr": "Bonjour Lea"]
            self.speaker.say(greetings[self.lastLanguage] ?? "Hello Lea", language: self.lastLanguage)
        }
    }

    private func handle(_ samples: [Float]) {
        preRoll.append(samples)
        guard recordingActive else { return }
        recording.append(contentsOf: samples)
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
                    self.panel?.send(event: event)
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
            state = .speaking
            speaker.onFinished = { [weak self] in
                guard let self, self.state == .speaking else { return }
                self.state = .idle
                self.wake.listensForStop = false
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
