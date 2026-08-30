import Foundation
import Security
import AgatEdgeCore

enum KeychainVaultError: Error {
    case unexpectedStatus(OSStatus)
    case invalidToken
}

actor KeychainVault: NodeCredentialVault {
    static let shared = KeychainVault()
    private let service = "io.agat.edge.node-credential.v1"
    private let account = "node-token"

    func save(_ token: String) throws {
        guard token.utf8.count >= 32, token.utf8.count <= 512,
              !token.contains("\n"), !token.contains("\r"), !token.contains("\0") else {
            throw KeychainVaultError.invalidToken
        }
        try deleteCredentials()
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrAccessible as String: kSecAttrAccessibleAfterFirstUnlockThisDeviceOnly,
            kSecAttrSynchronizable as String: false,
            kSecValueData as String: Data(token.utf8),
        ]
        let status = SecItemAdd(query as CFDictionary, nil)
        guard status == errSecSuccess else { throw KeychainVaultError.unexpectedStatus(status) }
    }

    func token() throws -> String? {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: false,
            kSecReturnData as String: true,
            kSecMatchLimit as String: kSecMatchLimitOne,
        ]
        var item: CFTypeRef?
        let status = SecItemCopyMatching(query as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data, let token = String(data: data, encoding: .utf8) else {
            throw KeychainVaultError.unexpectedStatus(status)
        }
        return token
    }

    func deleteCredentials() throws {
        let query: [String: Any] = [
            kSecClass as String: kSecClassGenericPassword,
            kSecAttrService as String: service,
            kSecAttrAccount as String: account,
            kSecAttrSynchronizable as String: kSecAttrSynchronizableAny,
        ]
        let status = SecItemDelete(query as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else {
            throw KeychainVaultError.unexpectedStatus(status)
        }
    }
}
