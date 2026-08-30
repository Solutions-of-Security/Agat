import Foundation

public protocol NodeCredentialVault: Sendable {
    func token() async throws -> String?
    func deleteCredentials() async throws
}

public struct RemoteWipeReport: Equatable, Sendable {
    public let generation: Int
    public let credentialsDeleted: Bool
    public let localDataDeleted: Bool
    public let acknowledged: Bool
}

public enum RemoteWipeError: Error, Equatable {
    case notAWipeCommand
    case credentialMissing
    case unsafeManagedPath
}

public enum RemoteWipeExecutor {
    public static func execute(
        command: ControlCommand,
        vault: NodeCredentialVault,
        managedRoot: URL,
        managedDirectories: [URL],
        clearConfiguration: @Sendable () async -> Void,
        acknowledge: @Sendable (_ inMemoryToken: String, _ generation: Int, _ localDataDeleted: Bool) async throws -> Void
    ) async throws -> RemoteWipeReport {
        guard command.action == "wipe", command.generation > 0 else { throw RemoteWipeError.notAWipeCommand }
        guard let token = try await vault.token(), !token.isEmpty else { throw RemoteWipeError.credentialMissing }
        let root = managedRoot.standardizedFileURL.resolvingSymlinksInPath()
        let safeDirectories = try managedDirectories.map { directory -> URL in
            let resolved = directory.standardizedFileURL.resolvingSymlinksInPath()
            let rootPrefix = root.path.hasSuffix("/") ? root.path : root.path + "/"
            guard resolved.path.hasPrefix(rootPrefix), resolved.path != root.path else {
                throw RemoteWipeError.unsafeManagedPath
            }
            return resolved
        }

        try await vault.deleteCredentials()
        await clearConfiguration()
        var localDataDeleted = true
        for directory in safeDirectories where FileManager.default.fileExists(atPath: directory.path) {
            do {
                try FileManager.default.removeItem(at: directory)
            } catch {
                localDataDeleted = false
            }
        }
        var acknowledged = false
        for attempt in 1...3 {
            do {
                try await acknowledge(token, command.generation, localDataDeleted)
                acknowledged = true
                break
            } catch {
                if attempt < 3 {
                    try? await Task.sleep(for: .milliseconds(250 * attempt))
                }
            }
        }
        return RemoteWipeReport(
            generation: command.generation,
            credentialsDeleted: true,
            localDataDeleted: localDataDeleted,
            acknowledged: acknowledged
        )
    }
}
