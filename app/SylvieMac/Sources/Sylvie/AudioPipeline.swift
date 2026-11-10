import AVFoundation

/// Owns the microphone and the speaker through one AVAudioEngine.
///
/// Voice processing is turned on for the input: macOS then cancels the sound the engine itself
/// plays (Sylvie's voice), so she doesn't hear herself. That's why speech is played through
/// `player` here and not through AVSpeechSynthesizer's own output.
final class AudioPipeline {
    let engine = AVAudioEngine()
    let player = AVAudioPlayerNode()
    /// 16 kHz mono samples, for voice detection and recording.
    var onSamples: (([Float]) -> Void)?
    /// Raw microphone buffers, for the speech recognizer that spots the wake word.
    var onBuffer: ((AVAudioPCMBuffer) -> Void)?

    let speechFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000, channels: 1, interleaved: false)!
    let playerFormat = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 24000, channels: 1, interleaved: false)!
    private var inputConverter: AVAudioConverter?
    private var outputConverters: [String: AVAudioConverter] = [:]
    private(set) var running = false

    /// False when macOS refused echo cancellation and the mic runs without it (see start()).
    private(set) var echoCancelled = true

    /// How loud the app itself played lately (mean square per buffer, with its time): tells Sylvie's own
    /// voice apart from Lea's in the mic (BargeInDetector).
    private var outputLevels: [(Date, Float)] = []
    private let outputLock = NSLock()

    /// Measured 2026-11-06 on Lea's MacBook: the voice-processing unit failed to initialise
    /// ("client-side input and output formats do not match", -10875), so the app never listened.
    /// Then it starts without echo cancellation instead of giving up.
    func start() throws {
        guard !running else { return }
        // Echo cancellation (Apple's voice processing) doesn't work on Lea's MacBook: it either fails to
        // start (-10875) or starts in a fault state and delivers silence ("failed to run downlink DSP",
        // 2026-11-06), so Sylvie heard nothing. The plain mic works; Sylvie ignores the mic while she
        // talks and turns music down while she listens (AppController, MusicDucker).
        if engine.inputNode.isVoiceProcessingEnabled { teardown() }
        try configure(voiceProcessing: false)
        echoCancelled = false
        running = true
    }

    private func configure(voiceProcessing: Bool) throws {
        let input = engine.inputNode
        if input.isVoiceProcessingEnabled != voiceProcessing {
            try input.setVoiceProcessingEnabled(voiceProcessing)
        }
        let inFormat = input.outputFormat(forBus: 0)
        inputConverter = AVAudioConverter(from: inFormat, to: speechFormat)
        inputConverter?.downmix = true

        if player.engine == nil { engine.attach(player) }
        engine.connect(player, to: engine.mainMixerNode, format: playerFormat)

        input.removeTap(onBus: 0)  // a failed earlier start may have left one (installing twice crashes)
        input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buffer, _ in
            guard let self else { return }
            if let samples = self.convertToSpeech(buffer) {
                self.onSamples?(samples)
                // The recognizer always gets 16 kHz mono: with echo cancellation on, the raw buffers
                // have 9 channels and it silently heard nothing (measured 2026-11-06).
                if let mono = self.monoBuffer(samples) { self.onBuffer?(mono) }
            }
        }
        let mixer = engine.mainMixerNode
        mixer.removeTap(onBus: 0)
        mixer.installTap(onBus: 0, bufferSize: 1024, format: nil) { [weak self] buffer, _ in
            guard let self, let data = buffer.floatChannelData, buffer.frameLength > 0 else { return }
            let n = Int(buffer.frameLength)
            var sum: Float = 0
            for i in 0..<n { sum += data[0][i] * data[0][i] }
            let now = Date()
            self.outputLock.lock()
            self.outputLevels.append((now, sum / Float(n)))
            self.outputLevels.removeAll { now.timeIntervalSince($0.0) > 1 }
            self.outputLock.unlock()
        }
        engine.prepare()
        try engine.start()
    }

    /// The loudest level the app played in the last `seconds` (long enough to cover the echo's delay).
    func recentOutputPower(seconds: TimeInterval = 0.3) -> Float {
        let now = Date()
        outputLock.lock()
        defer { outputLock.unlock() }
        return outputLevels.filter { now.timeIntervalSince($0.0) <= seconds }.map(\.1).max() ?? 0
    }

    private func teardown() {
        engine.inputNode.removeTap(onBus: 0)
        engine.mainMixerNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        engine.reset()
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        engine.mainMixerNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        running = false
    }

    private func monoBuffer(_ samples: [Float]) -> AVAudioPCMBuffer? {
        guard !samples.isEmpty,
              let b = AVAudioPCMBuffer(pcmFormat: speechFormat, frameCapacity: AVAudioFrameCount(samples.count)),
              let ch = b.floatChannelData?[0] else { return nil }
        b.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { ch.update(from: $0.baseAddress!, count: samples.count) }
        return b
    }

    private func convertToSpeech(_ buffer: AVAudioPCMBuffer) -> [Float]? {
        guard let converter = inputConverter else { return nil }
        let ratio = speechFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: speechFormat, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed {
                status.pointee = .noDataNow
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        guard error == nil, let data = out.floatChannelData else { return nil }
        return Array(UnsafeBufferPointer(start: data[0], count: Int(out.frameLength)))
    }

    /// Converts any buffer (e.g. from the speech synthesizer) to the player's format.
    func toPlayerFormat(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer? {
        if buffer.format == playerFormat { return buffer }
        let key = "\(buffer.format.sampleRate)-\(buffer.format.commonFormat.rawValue)-\(buffer.format.channelCount)"
        let converter = outputConverters[key] ?? AVAudioConverter(from: buffer.format, to: playerFormat)
        guard let converter else { return nil }
        outputConverters[key] = converter
        let ratio = playerFormat.sampleRate / buffer.format.sampleRate
        let capacity = AVAudioFrameCount(Double(buffer.frameLength) * ratio + 32)
        guard let out = AVAudioPCMBuffer(pcmFormat: playerFormat, frameCapacity: capacity) else { return nil }
        var fed = false
        var error: NSError?
        converter.convert(to: out, error: &error) { _, status in
            if fed {
                status.pointee = .noDataNow
                return nil
            }
            fed = true
            status.pointee = .haveData
            return buffer
        }
        return error == nil ? out : nil
    }
}
