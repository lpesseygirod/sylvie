import Foundation

/// Encodes mono Float32 samples (-1…1) as a 16-bit PCM WAV file in memory.
/// Audio never touches the disk: it goes straight from memory to the local server.
public enum WAV {
    public static func encode(samples: [Float], sampleRate: Int = 16000) -> Data {
        var data = Data()
        let byteRate = sampleRate * 2
        let dataSize = samples.count * 2

        func append<T: FixedWidthInteger>(_ value: T) {
            var v = value.littleEndian
            withUnsafeBytes(of: &v) { data.append(contentsOf: $0) }
        }

        data.append(contentsOf: Array("RIFF".utf8))
        append(UInt32(36 + dataSize))
        data.append(contentsOf: Array("WAVE".utf8))
        data.append(contentsOf: Array("fmt ".utf8))
        append(UInt32(16))          // PCM chunk size
        append(UInt16(1))           // PCM format
        append(UInt16(1))           // mono
        append(UInt32(sampleRate))
        append(UInt32(byteRate))
        append(UInt16(2))           // block align
        append(UInt16(16))          // bits per sample
        data.append(contentsOf: Array("data".utf8))
        append(UInt32(dataSize))
        data.reserveCapacity(data.count + dataSize)
        for s in samples {
            let clipped = max(-1, min(1, s))
            append(Int16(clipped * 32767))
        }
        return data
    }

    /// Decodes a 16-bit PCM mono WAV (what Sylvie's Kokoro voice sends) into Float32 samples.
    /// Walks the chunks instead of assuming a 44-byte header. Returns nil for anything else.
    public static func decode(_ data: Data) -> (samples: [Float], sampleRate: Int)? {
        let bytes = [UInt8](data)
        guard bytes.count >= 12, String(bytes: bytes[0..<4], encoding: .ascii) == "RIFF",
              String(bytes: bytes[8..<12], encoding: .ascii) == "WAVE" else { return nil }
        func u16(_ i: Int) -> Int { Int(bytes[i]) | Int(bytes[i + 1]) << 8 }
        func u32(_ i: Int) -> Int { u16(i) | u16(i + 2) << 16 }
        var i = 12, rate = 0, pcm16Mono = false
        while i + 8 <= bytes.count {
            let id = String(bytes: bytes[i..<i + 4], encoding: .ascii) ?? ""
            let size = u32(i + 4)
            let body = i + 8
            if id == "fmt " && body + 16 <= bytes.count {
                pcm16Mono = u16(body) == 1 && u16(body + 2) == 1 && u16(body + 14) == 16
                rate = u32(body + 4)
            } else if id == "data" {
                guard pcm16Mono, rate > 0 else { return nil }
                let end = min(bytes.count, body + size)
                var samples = [Float]()
                samples.reserveCapacity((end - body) / 2)
                var j = body
                while j + 1 < end {
                    samples.append(Float(Int16(bitPattern: UInt16(u16(j)))) / 32767)
                    j += 2
                }
                return (samples, rate)
            }
            i = body + size + (size & 1)
        }
        return nil
    }
}
