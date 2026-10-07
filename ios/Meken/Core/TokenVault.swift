import Foundation
import Security
import MekenCore

struct TokenVault: Sendable {
    let service: String
    let account: String
    init(service: String, account: String = "session") { self.service = service; self.account = account }
    private var query: [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: account]
    }
    func load() throws -> TokenPair? {
        try loadValue(TokenPair.self)
    }
    func loadValue<T: Decodable>(_ type: T.Type) throws -> T? {
        var search = query
        search[kSecReturnData as String] = true
        search[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(search as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw VaultError(status: status) }
        return try APIJSON.decoder().decode(type, from: data)
    }
    func save(_ tokens: TokenPair) throws {
        try saveValue(tokens)
    }
    func saveValue<T: Encodable>(_ value: T) throws {
        let data = try APIJSON.encoder().encode(value)
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
