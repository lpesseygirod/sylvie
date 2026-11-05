import Foundation

/// Keeps the last N samples of audio. Used as "pre-roll": when the wake word is heard, the
/// recording starts a little earlier, so "Sylvie, what's…" is captured from the beginning.
public struct RingBuffer {
    private var storage: [Float]
    private var start = 0
    public private(set) var count = 0
    public let capacity: Int

    public init(capacity: Int) {
        self.capacity = capacity
        self.storage = [Float](repeating: 0, count: capacity)
    }

    public mutating func append(_ samples: [Float]) {
        for x in samples.suffix(capacity) {
            let idx = (start + count) % capacity
            storage[idx] = x
            if count < capacity { count += 1 } else { start = (start + 1) % capacity }
        }
    }

    public func snapshot() -> [Float] {
        (0..<count).map { storage[(start + $0) % capacity] }
    }

    public mutating func clear() {
        start = 0
        count = 0
    }
}
