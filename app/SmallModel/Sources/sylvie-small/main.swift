// Usage:
//   sylvie-small --status          → {"available": true|false, "reason": "..."}
//   sylvie-small < request.json    → {"text": "..."} or {"error": "..."}
// request.json: {"instructions": "...", "prompt": "...", "max_tokens": 600, "temperature": 0.2}
// Nothing leaves the Mac: the model runs on-device; no network is used.
import Foundation
import FoundationModels

struct Request: Decodable {
    let instructions: String?
    let prompt: String
    let max_tokens: Int?
    let temperature: Double?
}

func emit(_ obj: [String: Any]) {
    let data = (try? JSONSerialization.data(withJSONObject: obj)) ?? Data("{}".utf8)
    FileHandle.standardOutput.write(data)
    FileHandle.standardOutput.write(Data("\n".utf8))
}

func availability() -> (Bool, String) {
    switch SystemLanguageModel.default.availability {
    case .available: return (true, "available")
    case .unavailable(let why): return (false, String(describing: why))
    @unknown default: return (false, "unknown")
    }
}

@main
struct SylvieSmall {
    static func main() async {
        if CommandLine.arguments.contains("--status") {
            let (ok, why) = availability()
            emit(["available": ok, "reason": why])
            return
        }
        let input = FileHandle.standardInput.readDataToEndOfFile()
        guard let req = try? JSONDecoder().decode(Request.self, from: input) else {
            emit(["error": "bad request"])
            exit(2)
        }
        let (ok, why) = availability()
        guard ok else {
            emit(["error": "unavailable", "reason": why])
            exit(3)
        }
        let session = LanguageModelSession(instructions: req.instructions ?? "")
        let options = GenerationOptions(temperature: req.temperature ?? 0.2,
                                        maximumResponseTokens: req.max_tokens ?? 600)
        do {
            let r = try await session.respond(to: req.prompt, options: options)
            emit(["text": r.content])
        } catch {
            emit(["error": String(describing: error)])
            exit(4)
        }
    }
}
