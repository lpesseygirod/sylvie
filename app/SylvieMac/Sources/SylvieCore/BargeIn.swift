import Foundation

/// Notices someone talking over Sylvie while she speaks, on a Mac where echo cancellation can't be used
/// (it's broken on Lea's MacBook, see AudioPipeline), so the microphone hears Sylvie's own voice too.
///
/// The app knows how loud it is playing. Sylvie's voice reaches the mic at a fairly steady fraction of
/// that level (her "echo ratio", learned at the start of each answer). Mic level beyond what her voice
/// explains, or speech during her pauses, is someone else talking.
///
/// Measured 2026-11-10: Lea said "Sylvie, stop" during a long answer and the speech recognizer
/// picked up nothing, because it mostly heard Sylvie. When this fires, the app turns Sylvie's voice
/// down so the recognizer can hear Lea's "stop" (or her name).
public struct BargeInDetector {
    public struct Settings {
        public var windowSamples = 1600      // 100 ms at 16 kHz
        public var marginDB: Float = 6       // louder than her own voice explains by this much…
        public var aboveNoiseDB: Float = 12  // …and clearly above the room's background
        public var windows = 2               // for 200 ms in a row
        public var learnWindows = 5          // her echo is learned for 0.5 s first
        public var history = 40              // echo ratios kept (the last 4 s of her speech)
        /// Her echo isn't steady (loud vowels come back louder than soft consonants), so the loud end of
        /// what she produced so far counts as "still her", not the middle.
        public var echoPercentile: Float = 0.9
        public init() {}
    }

    private let s: Settings
    private var pending: [Float] = []
    private var ratios: [Float] = []  // mic power ÷ output power, while only she was talking
    private var allRatios: [Float] = []  // every window of this answer, for the log
    private var run = 0
    public private(set) var detections = 0
    /// Levels when it last fired, in dB (for the log): mic, played, and what her echo explains (nil: a pause).
    public private(set) var lastFired: (mic: Float, played: Float, explained: Float?) = (0, 0, nil)

    public init(settings: Settings = Settings()) {
        self.s = settings
    }

    /// A new answer: learn her echo again (the volume or the room may have changed).
    public mutating func reset() {
        pending.removeAll()
        ratios.removeAll()
        allRatios.removeAll()
        run = 0
        detections = 0
    }

    /// How loud her echo was relative to what she played, over this answer (10th, 50th, 90th
    /// percentile, dB), for the log; nil until learned.
    public var echoSpreadDB: (low: Float, mid: Float, high: Float)? {
        guard allRatios.count >= s.learnWindows else { return nil }
        return (Self.db(Self.percentile(allRatios, 0.1)), Self.db(Self.percentile(allRatios, 0.5)),
                Self.db(Self.percentile(allRatios, 0.9)))
    }

    /// Feed mic samples (16 kHz mono). `outputPower`: the loudest mean-square level the app played in
    /// the last ~0.3 s (covers the echo's delay). `noiseFloorDB`: the room's background level, as
    /// VoiceActivityDetector tracks it. Returns true if someone is talking over her right now.
    public mutating func process(_ samples: [Float], outputPower: Float, noiseFloorDB: Float) -> Bool {
        pending.append(contentsOf: samples)
        var talking = false
        let noisePower = Self.power(fromDB: noiseFloorDB)
        while pending.count >= s.windowSamples {
            let window = pending[0..<s.windowSamples]
            pending.removeFirst(s.windowSamples)
            var sum: Float = 0
            for x in window { sum += x * x }
            let mic = sum / Float(s.windowSamples)
            let loud = mic > noisePower * Self.factor(s.aboveNoiseDB)
            var someone = false
            var explained: Float?
            if outputPower > noisePower {
                // She's audible: is there more than her own voice?
                allRatios.append(mic / outputPower)
                if ratios.count >= s.learnWindows {
                    explained = Self.percentile(ratios, s.echoPercentile) * outputPower
                    someone = loud && mic > explained! * Self.factor(s.marginDB)
                }
                if !someone {
                    ratios.append(mic / outputPower)
                    if ratios.count > s.history { ratios.removeFirst() }
                }
            } else {
                someone = loud  // a pause in her speech: any voice is someone else's
            }
            run = someone ? run + 1 : 0
            if run >= s.windows {
                if run == s.windows {
                    detections += 1
                    lastFired = (Self.db(mic), Self.db(outputPower), explained.map(Self.db))
                }
                talking = true
            }
        }
        return talking
    }

    static func factor(_ db: Float) -> Float { pow(10, db / 10) }
    static func db(_ power: Float) -> Float { 10 * log10(max(power, 1e-12)) }
    static func power(fromDB db: Float) -> Float { pow(10, db / 10) }

    static func percentile(_ xs: [Float], _ p: Float) -> Float {
        let sorted = xs.sorted()
        return sorted[min(sorted.count - 1, Int(Float(sorted.count) * p))]
    }
}
