import Foundation
import XCTest
@testable import AgatEdgeCore

final class AgatEdgeCoreTests: XCTestCase {
    func testConfigurationRejectsRemoteCleartext() throws {
        XCTAssertThrowsError(
            try EdgeConfiguration(
                coordinatorURL: try XCTUnwrap(URL(string: "http://coordinator.example")),
                nodeName: "iphone",
                modelName: "edge-model"
            )
        ) { error in
            XCTAssertEqual(error as? EdgeConfigurationError, .insecureCoordinatorURL)
        }
        let local = try EdgeConfiguration(
            coordinatorURL: try XCTUnwrap(URL(string: "http://127.0.0.1:8787")),
            nodeName: "iphone",
            modelName: "edge-model"
        )
        XCTAssertEqual(local.nodeName, "iphone")
    }

    func testAttestationBindingIsStableAndFullyBound() {
        let challenge = EnrollmentChallenge(
            schemaVersion: 1,
            id: "challenge-id",
            challenge: "challenge-value",
            expiresAt: Date(timeIntervalSince1970: 1_800_000_000),
            platform: "ios",
            applicationId: "TEAM.io.agat.edge"
        )
        let text = String(decoding: AttestationBinding.canonical(
            challenge: challenge,
            nodeName: "iphone",
            applicationId: "TEAM.io.agat.edge",
            keyId: "key-id"
        ), as: UTF8.self)
        XCTAssertEqual(text, "agat-edge-attestation-v1\nchallenge-id\nchallenge-value\niphone\nTEAM.io.agat.edge\nkey-id")
    }

    func testWipeDeletesCredentialsBeforeAcknowledgementAndOnlyInsideManagedRoot() async throws {
        actor Vault: NodeCredentialVault {
            var value: String? = "node-token"
            func token() -> String? { value }
            func deleteCredentials() { value = nil }
        }
        actor Recorder {
            var token: String?
            func record(_ value: String) { token = value }
        }
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        let models = root.appending(path: "models", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        try Data("model".utf8).write(to: models.appending(path: "edge.mlmodelc"))
        defer { try? FileManager.default.removeItem(at: root) }
        let vault = Vault()
        let recorder = Recorder()
        let report = try await RemoteWipeExecutor.execute(
            command: ControlCommand(schemaVersion: 1, action: "wipe", generation: 2, requestedAt: nil, reason: "lost"),
            vault: vault,
            managedRoot: root,
            managedDirectories: [models],
            clearConfiguration: {},
            acknowledge: { token, _, _ in
                let credentialAfterDeletion = await vault.token()
                XCTAssertNil(credentialAfterDeletion)
                await recorder.record(token)
            }
        )
        XCTAssertEqual(report, RemoteWipeReport(generation: 2, credentialsDeleted: true, localDataDeleted: true, acknowledged: true))
        let recordedToken = await recorder.token
        XCTAssertEqual(recordedToken, "node-token")
        XCTAssertFalse(FileManager.default.fileExists(atPath: models.path))
    }

    func testWipeRejectsPathsOutsideManagedRoot() async throws {
        actor Vault: NodeCredentialVault {
            func token() -> String? { "node-token" }
            func deleteCredentials() {}
        }
        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString)
        do {
            _ = try await RemoteWipeExecutor.execute(
                command: ControlCommand(schemaVersion: 1, action: "wipe", generation: 1, requestedAt: nil, reason: nil),
                vault: Vault(),
                managedRoot: root,
                managedDirectories: [FileManager.default.temporaryDirectory],
                clearConfiguration: {},
                acknowledge: { _, _, _ in }
            )
            XCTFail("Expected unsafeManagedPath")
        } catch {
            XCTAssertEqual(error as? RemoteWipeError, .unsafeManagedPath)
        }
    }

    func testWipeReportsUnacknowledgedAfterBoundedRetries() async throws {
        actor Vault: NodeCredentialVault {
            var value: String? = "node-token"
            func token() -> String? { value }
            func deleteCredentials() { value = nil }
        }
        actor Attempts {
            var count = 0
            func increment() { count += 1 }
        }
        enum ExpectedFailure: Error { case unavailable }

        let root = FileManager.default.temporaryDirectory.appending(path: UUID().uuidString, directoryHint: .isDirectory)
        let models = root.appending(path: "models", directoryHint: .isDirectory)
        try FileManager.default.createDirectory(at: models, withIntermediateDirectories: true)
        defer { try? FileManager.default.removeItem(at: root) }
        let attempts = Attempts()
        let report = try await RemoteWipeExecutor.execute(
            command: ControlCommand(schemaVersion: 1, action: "wipe", generation: 3, requestedAt: nil, reason: "offline"),
            vault: Vault(),
            managedRoot: root,
            managedDirectories: [models],
            clearConfiguration: {},
            acknowledge: { _, _, _ in
                await attempts.increment()
                throw ExpectedFailure.unavailable
            }
        )
        XCTAssertFalse(report.acknowledged)
        let attemptCount = await attempts.count
        XCTAssertEqual(attemptCount, 3)
    }
}
