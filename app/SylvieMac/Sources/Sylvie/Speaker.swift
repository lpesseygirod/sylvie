import AVFoundation

/// Speaks sentences one after another with the system voices, through the audio pipeline
/// (so echo cancellation works). Starts speaking as soon as the first sentence arrives.
final class Speaker {
    var voiceNames: [String: String] = ["en": "Samantha", "es": "Mónica", "fr": "Thomas"]
    var onFinished: (() -> Void)?
    private(set) var isSpeaking = false

    private let synth = AVSpeechSynthesizer()
    private let pipeline: AudioPipeline
    private var queue: [(text: String, language: String)] = []
    private var generation = 0  // bumps on stop(), so late callbacks from old speech are ignored

    init(pipeline: AudioPipeline) {
        self.pipeline = pipeline
    }

    func say(_ text: String, language: String) {
        let trimmed = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !trimmed.isEmpty else { return }
        queue.append((trimmed, language))
        if !isSpeaking { next() }
    }

    func stop() {
        generation += 1
        queue.removeAll()
        synth.stopSpeaking(at: .immediate)
        pipeline.player.stop()
        isSpeaking = false
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
            onFinished?()
            return
        }
        isSpeaking = true
        let item = queue.removeFirst()
        let utterance = AVSpeechUtterance(string: item.text)
        utterance.voice = voice(for: item.language)
        utterance.rate = AVSpeechUtteranceDefaultSpeechRate * 1.05
        let gen = generation
        if !pipeline.player.isPlaying { pipeline.player.play() }
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
