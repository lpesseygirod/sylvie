import Foundation

/// Finds "Sylvie" (and how speech recognizers tend to spell it) in a running transcript,
/// plus "stop" commands while Sylvie is talking.
public struct WakeMatcher {
    /// Spellings an English/Spanish/French recognizer produces for "Sylvie".
    public static let wakeVariants = ["sylvie", "silvie", "sylvi", "silvi", "silvy", "sylvy", "sylvee", "silvee", "selvie"]
    public static let stopWords = ["stop", "para", "parar", "basta", "arrête", "arrete", "stoppe", "cállate", "callate", "tais-toi"]

    public init() {}

    static func words(_ text: String) -> [String] {
        text.lowercased()
            .folding(options: [.diacriticInsensitive], locale: nil)
            .split(whereSeparator: { !$0.isLetter && $0 != "-" })
            .map(String.init)
    }

    /// Number of times the wake word occurs in the transcript.
    public func wakeCount(in transcript: String) -> Int {
        Self.words(transcript).filter { w in Self.wakeVariants.contains(w) }.count
    }

    /// True if the transcript ends with a stop command ("stop", "Sylvie stop", "para", "arrête").
    public func isStop(_ transcript: String) -> Bool {
        let ws = Self.words(transcript)
        guard let last = ws.last else { return false }
        let folded = Self.stopWords.map { $0.folding(options: [.diacriticInsensitive], locale: nil) }
        return folded.contains(last)
    }
}
