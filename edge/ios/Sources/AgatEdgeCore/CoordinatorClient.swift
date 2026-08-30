import Foundation
#if canImport(FoundationNetworking)
import FoundationNetworking
#endif

public enum CoordinatorClientError: Error, Equatable {
    case invalidResponse
    case responseTooLarge
    case http(Int, String)
    case unsupportedLease
}

private final class NoRedirectDelegate: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    func urlSession(
        _ session: URLSession,
        task: URLSessionTask,
        willPerformHTTPRedirection response: HTTPURLResponse,
        newRequest request: URLRequest,
        completionHandler: @escaping (URLRequest?) -> Void
    ) {
        completionHandler(nil)
    }
}

public actor CoordinatorClient {
    private let configuration: EdgeConfiguration
    private let session: URLSession
    private let encoder: JSONEncoder
    private let decoder: JSONDecoder

    public init(configuration: EdgeConfiguration, session: URLSession? = nil) {
        self.configuration = configuration
        if let session {
            self.session = session
        } else {
            let settings = URLSessionConfiguration.ephemeral
            settings.timeoutIntervalForRequest = 15
            settings.timeoutIntervalForResource = 75
            settings.httpCookieStorage = nil
            settings.urlCredentialStorage = nil
            self.session = URLSession(configuration: settings, delegate: NoRedirectDelegate(), delegateQueue: nil)
        }
        self.encoder = JSONEncoder()
        self.decoder = JSONDecoder()
        self.encoder.dateEncodingStrategy = .iso8601
        self.decoder.dateDecodingStrategy = .iso8601
    }

    public func requestChallenge(bootstrapToken: String, applicationId: String) async throws -> EnrollmentChallenge {
        struct Body: Encodable {
            let enrollmentToken: String
            let name: String
            let platform: String
            let applicationId: String
        }
        let data = try await request(
            path: "/api/v1/edge/enrollment/challenges",
            method: "POST",
            body: Body(enrollmentToken: bootstrapToken, name: configuration.nodeName, platform: "ios", applicationId: applicationId),
            expectedStatus: 201
        )
        return try decoder.decode(EnrollmentChallenge.self, from: data)
    }

    public func enroll(
        challenge: EnrollmentChallenge,
        evidence: AttestationEvidence,
        modelSizeBytes: Int64,
        memoryMb: Int
    ) async throws -> EnrollmentResponse {
        struct ModelProfile: Encodable {
            let name: String
            let provider = "coreml"
            let sizeBytes: Int64
            let quantization = "coreml"
            let capabilities = ["text"]
        }
        struct Body: Encodable {
            let challengeId: String
            let challenge: String
            let name: String
            let platform = "ios"
            let architecture = "arm64"
            let models: [String]
            let modelProfiles: [ModelProfile]
            let embeddingModels: [String] = []
            let maxConcurrency = 1
            let memoryMb: Int
            let gpu = "Core ML / Metal"
            let labels: [String: String]
            let attestation: AttestationEvidence
        }
        let body = Body(
            challengeId: challenge.id,
            challenge: challenge.challenge,
            name: configuration.nodeName,
            models: [configuration.modelName],
            modelProfiles: [ModelProfile(name: configuration.modelName, sizeBytes: modelSizeBytes)],
            memoryMb: memoryMb,
            labels: ["runtime": "ios-app", "inference": "coreml+metal", "background": "os-scheduled"],
            attestation: evidence
        )
        let data = try await request(path: "/api/v1/edge/enroll", method: "POST", body: body, expectedStatus: 201)
        return try decoder.decode(EnrollmentResponse.self, from: data)
    }

    public func heartbeat(token: String, batteryPercent: Double?, onBattery: Bool) async throws -> ControlCommand {
        struct Metrics: Encodable { let batteryPercent: Double?; let onBattery: Bool }
        struct Profile: Encodable {
            let name: String
            let provider = "coreml"
            let quantization = "coreml"
            let capabilities = ["text"]
        }
        struct Capabilities: Encodable {
            let models: [String]
            let modelProfiles: [Profile]
            let embeddingModels: [String] = []
            let agentRuntimes = ["single"]
            let agentRuntimeProfiles = ["tool_loop_v1"]
            let maxConcurrency = 1
            let labels = ["runtime": "ios-app", "inference": "coreml+metal", "background": "os-scheduled"]
        }
        struct Body: Encodable { let metrics: Metrics; let capabilities: Capabilities }
        let body = Body(
            metrics: Metrics(batteryPercent: batteryPercent, onBattery: onBattery),
            capabilities: Capabilities(models: [configuration.modelName], modelProfiles: [Profile(name: configuration.modelName)])
        )
        let data = try await request(path: "/api/v1/workers/heartbeat", method: "POST", body: body, bearer: token, expectedStatus: 200)
        return try decoder.decode(ControlCommand.self, from: data)
    }

    public func lease(token: String) async throws -> EdgeLease? {
        struct Body: Encodable { let workerVersion = "ios/1.6.0" }
        let result = try await rawRequest(path: "/api/v1/workers/lease", method: "POST", body: Body(), bearer: token)
        if result.status == 204 { return nil }
        guard result.status == 200 else { throw httpError(status: result.status, data: result.data) }
        let lease = try decoder.decode(EdgeLease.self, from: result.data)
        guard lease.activity == nil, lease.mcpTools.isEmpty, lease.agent.runtime == "single" else {
            throw CoordinatorClientError.unsupportedLease
        }
        return lease
    }

    public func renew(token: String, leaseId: String) async throws {
        try await emptyRequest(path: "/api/v1/leases/\(pathSegment(leaseId))/renew", body: Empty(), bearer: token, expectedStatus: 204)
    }

    public func complete(token: String, leaseId: String, output: String, durationMs: Int) async throws {
        struct Metrics: Encodable {
            let durationMs: Int
            let modelCalls = 1
            let provider = "coreml-metal"
            let model: String
        }
        struct Body: Encodable { let output: String; let metrics: Metrics }
        try await emptyRequest(
            path: "/api/v1/leases/\(pathSegment(leaseId))/complete",
            body: Body(output: output, metrics: Metrics(durationMs: durationMs, model: configuration.modelName)),
            bearer: token,
            expectedStatus: 200
        )
    }

    public func fail(token: String, leaseId: String, error: String) async throws {
        struct Body: Encodable { let error: String }
        let safe = String(error.replacingOccurrences(of: "\n", with: " ").replacingOccurrences(of: "\r", with: " ").prefix(2_000))
        try await emptyRequest(
            path: "/api/v1/leases/\(pathSegment(leaseId))/fail",
            body: Body(error: safe.isEmpty ? "Native inference failed" : safe),
            bearer: token,
            expectedStatus: 200
        )
    }

    public func acknowledgeWipe(token: String, generation: Int, localDataDeleted: Bool) async throws {
        struct Body: Encodable {
            let generation: Int
            let credentialsDeleted = true
            let localDataDeleted: Bool
        }
        try await emptyRequest(
            path: "/api/v1/edge/control/wipe-ack",
            body: Body(generation: generation, localDataDeleted: localDataDeleted),
            bearer: token,
            expectedStatus: 200
        )
    }

    private func emptyRequest<T: Encodable>(path: String, body: T, bearer: String?, expectedStatus: Int) async throws {
        _ = try await request(path: path, method: "POST", body: body, bearer: bearer, expectedStatus: expectedStatus)
    }

    private func request<T: Encodable>(
        path: String,
        method: String,
        body: T,
        bearer: String? = nil,
        expectedStatus: Int
    ) async throws -> Data {
        let result = try await rawRequest(path: path, method: method, body: body, bearer: bearer)
        guard result.status == expectedStatus else { throw httpError(status: result.status, data: result.data) }
        return result.data
    }

    private func rawRequest<T: Encodable>(path: String, method: String, body: T, bearer: String?) async throws -> (status: Int, data: Data) {
        guard let url = URL(string: path, relativeTo: configuration.coordinatorURL)?.absoluteURL else {
            throw EdgeConfigurationError.invalidCoordinatorURL
        }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = try encoder.encode(body)
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("agat-edge-ios/1.6.0", forHTTPHeaderField: "User-Agent")
        if let bearer { request.setValue("Bearer \(bearer)", forHTTPHeaderField: "Authorization") }
        let (data, response) = try await session.data(for: request)
        guard data.count <= 1_048_576 else { throw CoordinatorClientError.responseTooLarge }
        guard let response = response as? HTTPURLResponse else { throw CoordinatorClientError.invalidResponse }
        return (response.statusCode, data)
    }

    private func httpError(status: Int, data: Data) -> CoordinatorClientError {
        let detail = (try? JSONSerialization.jsonObject(with: data) as? [String: Any])?["error"] as? String ?? ""
        return .http(status, String(detail.prefix(500)))
    }

    private func pathSegment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics) ?? "invalid"
    }

    private struct Empty: Encodable {}
}
