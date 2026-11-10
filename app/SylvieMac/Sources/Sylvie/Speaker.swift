import AVFoundation
import SylvieCore

/// Speaks sentences one after another through the audio pipeline (so echo cancellation works).
/// Each sentence is fetched from Sylvie's Kokoro voice as soon as it arrives (so the next one is
/// ready while the current one plays); if that fails, the system voice says it instead.
@MainActor
final class Speaker {
    var voiceNames: [String: String] = ["en": "Samantha", "es": "Mónica", "fr": "Thomas"]
    var onFinished: (() -> Void)?
    var api: APIClient?
    private(set) var isSpeaking = false
    /// Sound is actually coming out (not just waiting for Kokoro's audio). Measured 2026-11-08: the
    /// greeting took 1.4-2.3 s to arrive, and the mic was ignored all that time, so Lea's question
    /// (started right after "Sylvie") was thrown away and she only heard "Hola Lea".
    private(set) var isPlaying = false

    private struct Item {
        let text: String
        let language: String
        let audio: Task<Data?, Never>?
    }

    private let synth = AVSpeechSynthesizer()
    private let pipeline: AudioPipeline
    private var queue: [Item] = []
    private var generation = 0  // bumps on stop(), so late callbacks from old speech are ignored

    init(pipeline: AudioPipeline) {
        self.pipeline = pipeline
    }

    func say(_ text: String, language: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        let audio = api.map { api in Task { await api.speak(trimmed, language: language) } }
        queue.append(Item(text: trimmed, language: language, audio: audio))
        if !isSpeaking { next() }
    }

    func stop() {
        generation += 1
        queue.forEach { $0.audio?.cancel() }
        queue.removeAll()
        synth.stopSpeaking(at: .immediate)
        pipeline.player.stop()
        pipeline.player.volume = 1
        isSpeaking = false
        isPlaying = false
    }

    /// Someone is talking over her: her voice goes down so the recognizer can hear "stop" (AppController).
    func setDucked(_ ducked: Bool) {
        pipeline.player.volume = ducked ? 0.15 : 1
    }

    private func voice(for language: String) -> AVSpeechSynthesisVoice? {
        let locales = ["en": "en-US", "es": "es-ES", "fr": "fr-FR"]
        let all = AVSpeechSynthesisVoice.speechVoices()
        if let name = voiceNames[language] {
            // Prefer the best installed quality of the chosen voice (Premium > Enhanced > Default).
            let named = all.filter { $0.name == name && $0.language.hasPrefix(language) }
            if let best = named.max(by: { $0.quality.rawValue < $1.quality.rawValue }) { return best }
        }
        let forLang = all.filter { $0.language == (locales[language] ?? "en-US") }
        return forLang.max(by: { $0.quality.rawValue < $1.quality.rawValue })
            ?? AVSpeechSynthesisVoice(language: locales[language] ?? "en-US")
    }

    private func next() {
        guard !queue.isEmpty else {
            isSpeaking = false
            isPlaying = false
            onFinished?()
            return
        }
        isSpeaking = true
        let item = queue.removeFirst()
        let gen = generation
        guard let audio = item.audio else { return systemSpeak(item, gen: gen) }
        Task { @MainActor [weak self] in
            let data = await audio.value
            guard let self, gen == self.generation else { return }
            if let data, let decoded = WAV.decode(data), self.play(decoded.samples, rate: decoded.sampleRate, gen: gen) {
                return
            }
            self.systemSpeak(item, gen: gen)
        }
    }

    /// Plays Kokoro's audio; returns false if it couldn't be converted (then the system voice speaks).
    private func play(_ samples: [Float], rate: Int, gen: Int) -> Bool {
        guard !samples.isEmpty,
              let format = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: Double(rate), channels: 1, interleaved: false),
              let buffer = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(samples.count)),
              let channel = buffer.floatChannelData?[0] else { return false }
        buffer.frameLength = AVAudioFrameCount(samples.count)
        samples.withUnsafeBufferPointer { channel.update(from: $0.baseAddress!, count: samples.count) }
        guard let converted = pipeline.toPlayerFormat(buffer) else { return false }
        if !pipeline.player.isPlaying { pipeline.player.play() }
        isPlaying = true
        pipeline.player.scheduleBuffer(converted, completionCallbackType: .dataPlayedBack) { [weak self] _ in
            DispatchQueue.main.async {
                guard let self, gen == self.generation else { return }
                self.next()
            }
        }
        return true
    }

    private func systemSpeak(_ item: Item, gen: Int) {
        let utterance = AVSpeechUtterance(string: item.text)
        utterance.voice = voice(for: item.language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        if !pipeline.player.isPlaying { pipeline.player.play() }
        isPlaying = true
        synth.write(utterance) { [weak self] buffer in
            guard let self, gen == self.generation, let pcm = buffer as? AVAudioPCMBuffer else { return }
            if pcm.frameLength == 0 {
                // End of this sentence: a tiny silent buffer whose completion means "all played".
                let marker = AVAudioPCMBuffer(pcmFormat: self.pipeline.playerFormat, frameCapacity: 240)!
                marker.frameLength = 240
                self.pipeline.player.scheduleBuffer(marker, completionCallbackType: .dataPlayedBack) { [weak self] _ in
                    DispatchQueue.main.async {
                        guard let self, gen == self.generation else { return }
                        self.next()
                    }
                }
                return
            }
            if let converted = self.pipeline.toPlayerFormat(pcm) {
                self.pipeline.player.scheduleBuffer(converted)
            }
        }
    }
}
