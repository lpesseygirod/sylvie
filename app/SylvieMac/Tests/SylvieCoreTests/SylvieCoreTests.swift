import ImageIO
import XCTest
@testable import SylvieCore

final class SylvieCoreTests: XCTestCase {
    func testSSEParser() {
        var p = SSEParser()
        XCTAssertNil(p.feed(line: "data: {\"type\": \"text\", \"text\": \"Hola\"}"))
        let ev = p.feed(line: "")
        XCTAssertEqual(ev?["type"] as? String, "text")
        XCTAssertEqual(ev?["text"] as? String, "Hola")
        XCTAssertNil(p.feed(line: ""))
        XCTAssertNil(p.feed(line: ": comment"))
    }

    func testWAVRoundTrip() {
        let wav = WAV.encode(samples: [0, 0.5, -0.5, 1.5], sampleRate: 24000)
        let decoded = WAV.decode(wav)
        XCTAssertEqual(decoded?.sampleRate, 24000)
        XCTAssertEqual(decoded?.samples.count, 4)
        XCTAssertEqual(decoded?.samples[1] ?? 0, 0.5, accuracy: 0.001)
        XCTAssertEqual(decoded?.samples[3] ?? 0, 1.0, accuracy: 0.001)
        XCTAssertNil(WAV.decode(Data("not a wav".utf8)))
    }

    func testWAVHeader() {
        let wav = WAV.encode(samples: [0, 0.5, -0.5, 1.5])
        XCTAssertEqual(wav.count, 44 + 8)
        XCTAssertEqual(String(data: wav.prefix(4), encoding: .ascii), "RIFF")
        XCTAssertEqual(String(data: wav[8..<12], encoding: .ascii), "WAVE")
        let last = wav.suffix(2).withUnsafeBytes { $0.load(as: Int16.self) }
        XCTAssertEqual(last, 32767)  // clipped
    }

    func testVADStartsAndEnds() {
        let vad = VoiceActivityDetector()
        let silence = [Float](repeating: 0.0005, count: 16000)
        let speech = (0..<16000).map { Float(sin(Double($0) * 0.05)) * 0.3 }
        XCTAssertEqual(vad.process(silence), [])
        XCTAssertEqual(vad.process(speech), [.speechStarted])
        XCTAssertEqual(vad.process(silence), [.speechEnded])
    }

    /// 2026-11-08: a floor learned in a quiet room, then louder room noise: the "utterance" never ended.
    func testVADEndsWhenTheBackgroundGotLouder() {
        let vad = VoiceActivityDetector()
        XCTAssertEqual(vad.process([Float](repeating: 0.0005, count: 16000)), [])  // quiet night
        let room = (0..<16000 * 3).map { Float(sin(Double($0) * 0.9)) * 0.01 }   // steady daytime noise
        let speech = (0..<16000).map { Float(sin(Double($0) * 0.05)) * 0.3 }
        let events = vad.process(speech) + vad.process(room)
        XCTAssertEqual(events, [.speechStarted, .speechEnded])
    }

    func testVADFloorFollowsTheRoomBetweenQuestions() {
        let vad = VoiceActivityDetector()
        XCTAssertEqual(vad.process([Float](repeating: 0.0005, count: 16000)), [])
        vad.reset()
        let room = (0..<16000 * 3).map { Float(sin(Double($0) * 0.9)) * 0.01 }
        vad.trackBackground(room)  // while she isn't recording
        XCTAssertEqual(vad.process(room), [])  // the room alone is not speech
    }

    func testVADNoSpeechTimeout() {
        var settings = VoiceActivityDetector.Settings()
        settings.noSpeechTimeoutSeconds = 1
        let vad = VoiceActivityDetector(settings: settings)
        let events = vad.process([Float](repeating: 0.0005, count: 20000))
        XCTAssertEqual(events, [.noSpeechTimeout])
    }

    func testWakeMatcher() {
        let m = WakeMatcher()
        XCTAssertEqual(m.wakeCount(in: "Hey Silvie what's up"), 1)
        XCTAssertEqual(m.wakeCount(in: "Sylvia is my friend"), 0)  // a different name
        XCTAssertEqual(m.wakeCount(in: "Sylvie… ¿Sylvie?"), 2)
        XCTAssertTrue(m.isStop("Sylvie, stop"))
        XCTAssertTrue(m.isStop("arrête"))
        XCTAssertFalse(m.isStop("stop the music and play jazz"))
        XCTAssertTrue(m.isStrongStop("los años bisiestos tienen stop"))
        XCTAssertFalse(m.isStrongStop("esto es para"))  // "para" alone never stops her mid-sentence
        XCTAssertFalse(m.isStrongStop("the bus stop is near the station"))
        XCTAssertEqual(m.strongStopCount(in: "los años bisiestos stop tienen un día más"), 1)  // Lea's stop mid-speech
        XCTAssertEqual(m.strongStopCount(in: "esto es para ti"), 0)
    }

    func testStopListening() {
        let m = WakeMatcher()
        XCTAssertTrue(m.isStopListening("Sylvie stop listening"))
        XCTAssertTrue(m.isStopListening("deja de escuchar"))
        XCTAssertTrue(m.isStopListening("Sylvie para de escucharme"))
        XCTAssertTrue(m.isStopListening("arrête d'écouter"))
        XCTAssertFalse(m.isStopListening("stop"))
        XCTAssertFalse(m.isStopListening("I love to listen to music, stop"))  // "stop" after, not before
        XCTAssertTrue(m.isListenTail("listening"))
        XCTAssertTrue(m.isListenTail("de escuchar"))
        XCTAssertFalse(m.isListenTail("can you listen to this song with me please"))
    }

    func testRingBuffer() {
        var r = RingBuffer(capacity: 3)
        r.append([1, 2])
        XCTAssertEqual(r.snapshot(), [1, 2])
        r.append([3, 4, 5])
        XCTAssertEqual(r.snapshot(), [3, 4, 5])
    }

    func testOrbGlyphStates() throws {
        for state in OrbState.allCases {
            let img = try XCTUnwrap(OrbGlyph.render(state: state, time: 0.3, pixels: 36))
            XCTAssertEqual(img.width, 36)
            let (r, g, b, a) = try pixel(img, x: 18, y: 18)
            XCTAssertGreaterThan(a, 100, "\(state) should be visible in the middle")
            if state == .muted {
                XCTAssertLessThan(abs(Int(r) - Int(b)), 40, "muted is grey")
            } else {
                XCTAssertGreaterThan(Int(b) + Int(g) + Int(r), 400, "\(state) has a bright core")
            }
        }
        // Moving states change over time; idle barely does.
        XCTAssertTrue(OrbState.thinking.animates && !OrbState.idle.animates && !OrbState.muted.animates)
        let a = try XCTUnwrap(OrbGlyph.render(state: .thinking, time: 0, pixels: 36))
        let b = try XCTUnwrap(OrbGlyph.render(state: .thinking, time: 0.7, pixels: 36))
        XCTAssertNotEqual(a.dataProvider?.data as Data?, b.dataProvider?.data as Data?)
        if let dir = ProcessInfo.processInfo.environment["SYLVIE_GLYPH_DIR"] {  // for design screenshots
            for state in OrbState.allCases {
                try writePNG(XCTUnwrap(OrbGlyph.render(state: state, time: 0.3, pixels: 144)),
                             to: URL(fileURLWithPath: dir).appendingPathComponent("glyph-\(state.rawValue).png"))
            }
        }
    }

    // MARK: Talking over Sylvie (BargeInDetector)

    /// A deterministic "voice": noise in syllables of ~200 ms with short gaps, at a varying loudness.
    private func voice(seconds: Double, amplitude: Float, seed: UInt64) -> [Float] {
        var state = seed
        func rnd() -> Float {
            state = state &* 6364136223846793005 &+ 1442695040888963407
            return Float(Int64(bitPattern: state >> 11) % 2000) / 1000 - 1
        }
        let n = Int(seconds * 16000)
        return (0..<n).map { i in
            let syllable = (i / 3200) % 5 == 4 ? Float(0.1) : 0.6 + 0.4 * sin(Float(i) / 900)
            return amplitude * syllable * rnd()
        }
    }

    /// Runs the detector over 20 ms chunks: the mic hears her echo (delayed 60 ms, at `echo` of her
    /// level), room noise, and `other` (someone else). Returns the chunk indexes where it fired.
    private func runBargeIn(output: [Float], other: [Float], echo: Float = 0.3) -> [Int] {
        var d = BargeInDetector()
        let delay = 960
        var noiseState: UInt64 = 7
        var fired: [Int] = []
        var powers: [Float] = []  // output power per chunk
        for c in 0..<(output.count / 320) {
            let range = (c * 320)..<((c + 1) * 320)
            powers.append(range.reduce(0) { $0 + output[$1] * output[$1] } / 320)
            let mic: [Float] = range.map { i in
                noiseState = noiseState &* 6364136223846793005 &+ 1
                let noise = Float(Int64(bitPattern: noiseState >> 11) % 2000) / 1000 - 1
                let heard = i >= delay ? output[i - delay] * echo : 0
                return heard + (i < other.count ? other[i] : 0) + 0.002 * noise
            }
            let recent = powers.suffix(15).max() ?? 0  // the last 0.3 s
            if d.process(mic, outputPower: recent, noiseFloorDB: -57) { fired.append(c) }
        }
        return fired
    }

    func testBargeInIgnoresHerOwnVoice() {
        let her = voice(seconds: 8, amplitude: 0.4, seed: 1)
        XCTAssertEqual(runBargeIn(output: her, other: []), [], "her own echo is not someone else")
    }

    func testBargeInHearsLeaOverHerVoice() {
        let her = voice(seconds: 6, amplitude: 0.4, seed: 1)
        // Lea says "Sylvie, stop" at 3 s, about as loud at the mic as Sylvie's echo is.
        let lea = [Float](repeating: 0, count: 48000) + voice(seconds: 0.8, amplitude: 0.25, seed: 9)
        let fired = runBargeIn(output: her, other: lea)
        XCTAssertFalse(fired.isEmpty, "Lea talking over her is heard")
        XCTAssertGreaterThanOrEqual(fired.first ?? 0, 150, "not before Lea starts (3 s)")
        XCTAssertLessThanOrEqual(fired.first ?? 999, 165, "within 0.3 s of Lea starting")
    }

    func testBargeInDuringHerPause() {
        // She finished a sentence (silence, the next one is still being prepared) and Lea speaks.
        let her = voice(seconds: 2, amplitude: 0.4, seed: 1) + [Float](repeating: 0, count: 32000)
        let lea = [Float](repeating: 0, count: 44800) + voice(seconds: 0.6, amplitude: 0.1, seed: 5)
        let fired = runBargeIn(output: her, other: lea)
        XCTAssertFalse(fired.isEmpty)
        XCTAssertGreaterThanOrEqual(fired.first ?? 0, 140)
        // Room noise alone during her pause is nothing.
        XCTAssertEqual(runBargeIn(output: her, other: []), [])
    }

    func testBargeInIgnoresQuietBackgroundTalk() {
        // Someone far away (10 dB below the threshold above the room noise) while she talks.
        let her = voice(seconds: 6, amplitude: 0.4, seed: 1)
        let far = [Float](repeating: 0, count: 48000) + voice(seconds: 1, amplitude: 0.004, seed: 3)
        XCTAssertEqual(runBargeIn(output: her, other: far), [])
    }

    private func pixel(_ img: CGImage, x: Int, y: Int) throws -> (UInt8, UInt8, UInt8, UInt8) {
        let data = try XCTUnwrap(img.dataProvider?.data as Data?)
        let i = y * img.bytesPerRow + x * 4
        return (data[i], data[i + 1], data[i + 2], data[i + 3])
    }

    private func writePNG(_ img: CGImage, to url: URL) throws {
        let dest = try XCTUnwrap(CGImageDestinationCreateWithURL(url as CFURL, "public.png" as CFString, 1, nil))
        CGImageDestinationAddImage(dest, img, nil)
        XCTAssertTrue(CGImageDestinationFinalize(dest))
    }
}
