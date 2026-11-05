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
}
