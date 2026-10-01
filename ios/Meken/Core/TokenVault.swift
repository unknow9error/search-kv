import Foundation
import Security
import MekenCore

struct TokenVault: Sendable {
    let service: String
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: "session"]
    }
    func load() throws -> TokenPair? {
        var search = query
        search[kSecReturnData as String] = true
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(search as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw VaultError(status: status) }
        return try APIJSON.decoder().decode(TokenPair.self, from: data)
    }
    func save(_ tokens: TokenPair) throws {
        let data = try APIJSON.encoder().encode(tokens)
        let changes: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly]
        let status = SecItemUpdate(query as CFDictionary, changes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query
            changes.forEach { item[$0.key] = $0.value }
            let result = SecItemAdd(item as CFDictionary, nil)
            guard result == errSecSuccess else { throw VaultError(status: result) }
        } else if status != errSecSuccess { throw VaultError(status: status) }
    }
    func clear() throws {
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw VaultError(status: status) }
    }
}

struct VaultError: Error, LocalizedError {
    let status: OSStatus
    var errorDescription: String? { "Не удалось сохранить сессию на устройстве. Попробуйте после разблокировки." }
}
