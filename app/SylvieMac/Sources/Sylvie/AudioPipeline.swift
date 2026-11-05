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

    func start() throws {
        guard !running else { return }
        let input = engine.inputNode
        try input.setVoiceProcessingEnabled(true)
        let inFormat = input.outputFormat(forBus: 0)
        inputConverter = AVAudioConverter(from: inFormat, to: speechFormat)
        inputConverter?.downmix = true

        engine.attach(player)
        engine.connect(player, to: engine.mainMixerNode, format: playerFormat)

        input.installTap(onBus: 0, bufferSize: 1024, format: inFormat) { [weak self] buffer, _ in
            guard let self else { return }
            self.onBuffer?(buffer)
            if let samples = self.convertToSpeech(buffer) {
                self.onSamples?(samples)
            }
        }
        engine.prepare()
        try engine.start()
        running = true
    }

    func stop() {
        guard running else { return }
        engine.inputNode.removeTap(onBus: 0)
        player.stop()
        engine.stop()
        running = false
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
