import Foundation
import Security

/// Reads the local API token that the Python server created in the Keychain (service "Sylvie").
/// The token is never logged or shown.
enum Keychain {
    static let service = "Sylvie"
    static let account = "local-api-token"

    static func token() -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var out: CFTypeRef?
        guard SecItemCopyMatching(query as CFDictionary, &out) == errSecSuccess,
              let data = out as? Data else { return nil }
        return String(data: data, encoding: .utf8)
    }
}
