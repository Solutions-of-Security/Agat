import CryptoKit
import DeviceCheck
import Foundation
import AgatEdgeCore

enum AppAttestorError: Error {
    case unsupported
    case missingKey
}

actor AppAttestor {
    static let shared = AppAttestor()
    private let service = DCAppAttestService.shared
    private let keyPreference = "agat.app-attest-key-id.v1"

    func attestation(
        challenge: EnrollmentChallenge,
        nodeName: String,
        applicationId: String
    ) async throws -> AttestationEvidence {
        guard service.isSupported else { throw AppAttestorError.unsupported }
        let keyId = try await keyID()
        let binding = AttestationBinding.canonical(
            challenge: challenge,
            nodeName: nodeName,
            applicationId: applicationId,
            keyId: keyId
        )
        let clientDataHash = Data(SHA256.hash(data: binding))
        let object: Data = try await withCheckedThrowingContinuation { continuation in
            service.attestKey(keyId, clientDataHash: clientDataHash) { data, error in
                if let error { continuation.resume(throwing: error) }
                else if let data { continuation.resume(returning: data) }
                else { continuation.resume(throwing: AppAttestorError.missingKey) }
            }
        }
        return AttestationEvidence(
            provider: "app_attest",
            token: object.base64URLEncodedString(),
            keyId: keyId,
            applicationId: applicationId
        )
    }

    func wipeKeyReference() {
        UserDefaults.standard.removeObject(forKey: keyPreference)
    }

    private func keyID() async throws -> String {
        if let existing = UserDefaults.standard.string(forKey: keyPreference), !existing.isEmpty { return existing }
        let generated: String = try await withCheckedThrowingContinuation { continuation in
            service.generateKey { keyId, error in
                if let error { continuation.resume(throwing: error) }
                else if let keyId { continuation.resume(returning: keyId) }
                else { continuation.resume(throwing: AppAttestorError.missingKey) }
            }
        }
        UserDefaults.standard.set(generated, forKey: keyPreference)
        return generated
    }
}

private extension Data {
    func base64URLEncodedString() -> String {
        base64EncodedString()
            .replacingOccurrences(of: "+", with: "-")
            .replacingOccurrences(of: "/", with: "_")
            .replacingOccurrences(of: "=", with: "")
    }
}
