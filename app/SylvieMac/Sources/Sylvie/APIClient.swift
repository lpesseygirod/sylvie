import Foundation
import SylvieCore

/// Talks to Sylvie's local server on 127.0.0.1 with the Keychain token.
final class APIClient {
    let base: URL
    private let token: String
    private let session: URLSession

    init(token: String, port: Int = 8765) {
        self.token = token
        self.base = URL(string: "http://127.0.0.1:\(port)")!
        let config = URLSessionConfiguration.ephemeral  // no disk cache of conversations
        config.timeoutIntervalForRequest = 300
        self.session = URLSession(configuration: config)
    }

    private func request(_ path: String, method: String = "GET") -> URLRequest {
        var req = URLRequest(url: URL(string: path, relativeTo: base)!)
        req.httpMethod = method
        req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        return req
    }

    func isReady() async -> Bool {
        guard let (data, resp) = try? await session.data(for: request("/api/health")),
              (resp as? HTTPURLResponse)?.statusCode == 200,
              let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any] else { return false }
        return obj["ready"] as? Bool ?? false
    }

    /// Sends one recorded utterance; streams back the server's events (transcript, text, speak, done…).
    func voiceTurn(wav: Data, conversationId: String?) -> AsyncThrowingStream<[String: Any], Error> {
        var path = "/api/voice/turn"
        if let cid = conversationId, let enc = cid.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed) {
            path += "?conversation_id=\(enc)"
        }
        var req = request(path, method: "POST")
        req.setValue("audio/wav", forHTTPHeaderField: "Content-Type")
        req.httpBody = wav
        let session = self.session
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    let (bytes, resp) = try await session.bytes(for: req)
                    guard (resp as? HTTPURLResponse)?.statusCode == 200 else {
                        throw URLError(.badServerResponse)
                    }
                    var parser = SSEParser()
                    for try await line in bytes.lines {
                        // The server sends one `data:` line per event, so each one is complete.
                        _ = parser.feed(line: line)
                        if line.hasPrefix("data:"), let ev = parser.feed(line: "") {
                            continuation.yield(ev)
                        }
                    }
                    continuation.finish()
                } catch {
                    continuation.finish(throwing: error)
                }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    /// One sentence as WAV from Sylvie's Kokoro voice, or nil (system voice selected, or any error).
    func speak(_ text: String, language: String) async -> Data? {
        var req = request("/api/voice/speak", method: "POST")
        req.setValue("application/json", forHTTPHeaderField: "Content-Type")
        req.timeoutInterval = 15
        req.httpBody = try? JSONSerialization.data(withJSONObject: ["text": text, "language": language])
        guard let (data, resp) = try? await session.data(for: req),
              (resp as? HTTPURLResponse)?.statusCode == 200 else { return nil }
        return data
    }

    func stop(conversationId: String) {
        let req = request("/api/chat/\(conversationId)/stop", method: "POST")
        session.dataTask(with: req).resume()
    }
}
