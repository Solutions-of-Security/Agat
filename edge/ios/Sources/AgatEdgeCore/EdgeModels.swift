import Foundation

public enum EdgeConfigurationError: Error, Equatable {
    case invalidCoordinatorURL
    case insecureCoordinatorURL
    case invalidNodeName
    case invalidModelName
}

public struct EdgeConfiguration: Codable, Equatable, Sendable {
    public let coordinatorURL: URL
    public let nodeName: String
    public let modelName: String

    public init(coordinatorURL: URL, nodeName: String, modelName: String) throws {
        guard let scheme = coordinatorURL.scheme?.lowercased(), let host = coordinatorURL.host?.lowercased(),
              coordinatorURL.user == nil, coordinatorURL.password == nil,
              coordinatorURL.query == nil, coordinatorURL.fragment == nil else {
            throw EdgeConfigurationError.invalidCoordinatorURL
        }
        let loopback = ["127.0.0.1", "localhost", "::1"].contains(host)
        guard scheme == "https" || (scheme == "http" && loopback) else {
            throw EdgeConfigurationError.insecureCoordinatorURL
        }
        let normalizedName = nodeName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedName.isEmpty, normalizedName.utf8.count <= 120,
              normalizedName.unicodeScalars.allSatisfy({ $0.value != 0 && $0.value != 10 && $0.value != 13 }) else {
            throw EdgeConfigurationError.invalidNodeName
        }
        let normalizedModel = modelName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !normalizedModel.isEmpty, normalizedModel.utf8.count <= 200,
              normalizedModel.unicodeScalars.allSatisfy({ $0.value != 0 && $0.value != 10 && $0.value != 13 }) else {
            throw EdgeConfigurationError.invalidModelName
        }
        self.coordinatorURL = coordinatorURL.absoluteURL
        self.nodeName = normalizedName
        self.modelName = normalizedModel
    }
}

public struct EnrollmentChallenge: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let id: String
    public let challenge: String
    public let expiresAt: Date
    public let platform: String
    public let applicationId: String
}

public struct EnrollmentResponse: Codable, Equatable, Sendable {
    public let id: String
    public let token: String
}

public struct ControlCommand: Codable, Equatable, Sendable {
    public let schemaVersion: Int
    public let action: String
    public let generation: Int
    public let requestedAt: Date?
    public let reason: String?
}

public struct EdgeLease: Codable, Equatable, Sendable {
    public struct Run: Codable, Equatable, Sendable {
        public let id: String
        public let name: String
        public let input: String
    }

    public struct Agent: Codable, Equatable, Sendable {
        public let id: String
        public let name: String
        public let systemPrompt: String
        public let model: String?
        public let runtime: String
    }

    public struct ContextEntry: Codable, Equatable, Sendable {
        public let agentName: String
        public let output: String
    }

    public let leaseId: String
    public let expiresAt: Date
    public let run: Run
    public let agent: Agent
    public let context: [ContextEntry]
    public let mcpTools: [OpaqueTool]
    public let activity: OpaqueActivity?
}

public struct OpaqueTool: Codable, Equatable, Sendable {}
public struct OpaqueActivity: Codable, Equatable, Sendable {}

public struct AttestationEvidence: Codable, Equatable, Sendable {
    public let provider: String
    public let token: String
    public let keyId: String
    public let applicationId: String

    public init(provider: String, token: String, keyId: String, applicationId: String) {
        self.provider = provider
        self.token = token
        self.keyId = keyId
        self.applicationId = applicationId
    }
}

public enum AttestationBinding {
    public static func canonical(
        challenge: EnrollmentChallenge,
        nodeName: String,
        applicationId: String,
        keyId: String
    ) -> Data {
        Data([
            "agat-edge-attestation-v1",
            challenge.id,
            challenge.challenge,
            nodeName,
            applicationId,
            keyId,
        ].joined(separator: "\n").utf8)
    }
}
