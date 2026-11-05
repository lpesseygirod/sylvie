import Foundation

/// Energy-based voice activity detector with an adaptive noise floor.
///
/// Decides when Lea started and stopped talking. Works on 20 ms frames of 16 kHz mono audio.
/// (The plan names Silero VAD; this simpler detector is the first version — see docs/decisions.md.)
public final class VoiceActivityDetector {
    public enum Event: Equatable {
        case speechStarted
        case speechEnded
        case noSpeechTimeout
        case maxLengthReached
    }

    public struct Settings {
        public var frameSamples = 320                 // 20 ms at 16 kHz
        public var startMarginDB: Float = 10          // above the noise floor
        public var startFrames = 3                    // 60 ms of loud frames to start
        public var endSilenceSeconds: Double = 0.7    // silence that ends an utterance
        public var noSpeechTimeoutSeconds: Double = 6 // give up if Lea says nothing
        public var maxSeconds: Double = 30
        public init() {}
    }

    public private(set) var inSpeech = false
    public private(set) var noiseFloorDB: Float = -60
    private let s: Settings
    private var loudRun = 0
    private var silentFrames = 0
    private var framesSinceStart = 0
    private var speechFrames = 0
    private var pending: [Float] = []

    public init(settings: Settings = Settings()) {
        self.s = settings
    }

    public func reset(keepNoiseFloor: Bool = true) {
        inSpeech = false
        loudRun = 0
        silentFrames = 0
        framesSinceStart = 0
        speechFrames = 0
        pending.removeAll()
        if !keepNoiseFloor { noiseFloorDB = -60 }
    }

    public static func levelDB(_ frame: ArraySlice<Float>) -> Float {
        guard !frame.isEmpty else { return -100 }
        var sum: Float = 0
        for x in frame { sum += x * x }
        let rms = (sum / Float(frame.count)).squareRoot()
        return 20 * log10(max(rms, 1e-7))
    }

    /// Feed any number of samples; returns the events they caused, in order.
    public func process(_ samples: [Float]) -> [Event] {
        pending.append(contentsOf: samples)
        var events: [Event] = []
        let frameSeconds = Double(s.frameSamples) / 16000
        while pending.count >= s.frameSamples {
            let frame = pending[0..<s.frameSamples]
            let db = Self.levelDB(frame)
            pending.removeFirst(s.frameSamples)
            framesSinceStart += 1

            if !inSpeech {
                // Track the background noise when nobody is talking (slowly up, quickly down).
                let rate: Float = db > noiseFloorDB ? 0.02 : 0.2
                noiseFloorDB += (db - noiseFloorDB) * rate
                if db > noiseFloorDB + s.startMarginDB {
                    loudRun += 1
                    if loudRun >= s.startFrames {
                        inSpeech = true
                        silentFrames = 0
                        speechFrames = loudRun
                        events.append(.speechStarted)
                    }
                } else {
                    loudRun = 0
                }
                if !inSpeech && Double(framesSinceStart) * frameSeconds >= s.noSpeechTimeoutSeconds {
                    events.append(.noSpeechTimeout)
                    framesSinceStart = 0
                }
            } else {
                speechFrames += 1
                if db > noiseFloorDB + s.startMarginDB * 0.6 {
                    silentFrames = 0
                } else {
                    silentFrames += 1
                    if Double(silentFrames) * frameSeconds >= s.endSilenceSeconds {
                        inSpeech = false
                        loudRun = 0
                        events.append(.speechEnded)
                    }
                }
                if inSpeech && Double(speechFrames) * frameSeconds >= s.maxSeconds {
                    inSpeech = false
                    events.append(.maxLengthReached)
                }
            }
        }
        return events
    }
}
