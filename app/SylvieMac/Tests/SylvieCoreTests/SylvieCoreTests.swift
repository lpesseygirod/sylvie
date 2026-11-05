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
    }

    func testRingBuffer() {
        var r = RingBuffer(capacity: 3)
        r.append([1, 2])
        XCTAssertEqual(r.snapshot(), [1, 2])
        r.append([3, 4, 5])
        XCTAssertEqual(r.snapshot(), [3, 4, 5])
    }
}
