import Foundation

/// Incremental parser for the server's Server-Sent Events stream.
/// Feed it lines as they arrive; it returns each `data:` payload decoded as a JSON object.
public struct SSEParser {
    private var dataLines: [String] = []

    public init() {}

    /// Feed one line (without the trailing newline). Returns an event when a blank line ends one.
    public mutating func feed(line: String) -> [String: Any]? {
        if line.isEmpty {
            defer { dataLines.removeAll() }
            guard !dataLines.isEmpty else { return nil }
            let payload = dataLines.joined(separator: "\n")
            guard let data = payload.data(using: .utf8),
                  let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return nil }
            return obj
        }
        if line.hasPrefix("data:") {
            var value = String(line.dropFirst(5))
            if value.hasPrefix(" ") { value.removeFirst() }
            dataLines.append(value)
        }
        return nil
    }
}
