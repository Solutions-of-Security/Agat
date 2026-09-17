import Foundation
import SwiftUI
import UIKit
import AgatEdgeCore

@MainActor
final class WorkerController: ObservableObject {
    static let shared = WorkerController()

    @Published var coordinatorURL = ""
    @Published var nodeName = UIDevice.current.name
    @Published var modelName = "edge-coreml"
    @Published var applicationId = ""
    @Published private(set) var status = "Not enrolled"
    @Published private(set) var running = false
    @Published private(set) var modelReady = FileManager.default.fileExists(atPath: ModelStore.shared.compiledModelURL.path)

    private var loop: Task<Void, Never>?
    private var engine: (any TextInferenceEngine)?
    private var credentialWiped = false

    private init() {
        UIDevice.current.isBatteryMonitoringEnabled = true
        Task { await restore() }
    }

    func restore() async {
        if let (configuration, appId) = try? await ConfigurationStore.shared.load() {
            coordinatorURL = configuration.coordinatorURL.absoluteString
            nodeName = configuration.nodeName
            modelName = configuration.modelName
            applicationId = appId
        }
        status = (try? await KeychainVault.shared.token()) == nil ? "Not enrolled" : "Credential stored in ThisDeviceOnly Keychain"
    }

    func importModel(_ url: URL) async {
        do {
            try ModelStore.shared.importModel(from: url)
            modelReady = true
            engine = nil
            status = "Core ML model imported (\(ModelStore.shared.sizeBytes()) bytes)"
        } catch {
            status = "Model import failed: \(error.localizedDescription)"
        }
    }

    func enroll(bootstrapToken: String) async {
        do {
            guard modelReady, ModelStore.shared.sizeBytes() > 0 else { throw TextInferenceError.unavailable }
            guard !bootstrapToken.isEmpty else { throw AppAttestorError.missingKey }
            let configuration = try currentConfiguration()
            guard validApplicationId(applicationId) else { throw EdgeConfigurationError.invalidNodeName }
            status = "Requesting one-time challenge…"
            let client = CoordinatorClient(configuration: configuration)
            let challenge = try await client.requestChallenge(bootstrapToken: bootstrapToken, applicationId: applicationId)
            status = "Generating App Attest evidence…"
            let evidence = try await AppAttestor.shared.attestation(
                challenge: challenge,
                nodeName: configuration.nodeName,
                applicationId: applicationId
            )
            let enrolled = try await client.enroll(
                challenge: challenge,
                evidence: evidence,
                modelSizeBytes: ModelStore.shared.sizeBytes(),
                memoryMb: Int(ProcessInfo.processInfo.physicalMemory / 1_048_576)
            )
            try await KeychainVault.shared.save(enrolled.token)
            try await ConfigurationStore.shared.save(configuration, applicationId: applicationId)
            credentialWiped = false
            status = "Hardware-attested node \(enrolled.id.prefix(8))… enrolled"
            BackgroundWorker.schedule()
        } catch {
            status = "Enrollment failed: \(error.localizedDescription)"
        }
    }

    func startForeground() {
        guard loop == nil else { return }
        running = true
        loop = Task { [weak self] in
            guard let self else { return }
            while !Task.isCancelled {
                _ = await self.processOnce()
                if self.credentialWiped { break }
                try? await Task.sleep(for: .seconds(2))
            }
            self.loop = nil
            self.running = false
        }
        status = "Foreground worker active"
    }

    func stop() {
        loop?.cancel()
        loop = nil
        running = false
        status = "Worker stopped"
    }

    @discardableResult
    func processOnce() async -> Bool {
        do {
            guard let (configuration, _) = try await ConfigurationStore.shared.load(),
                  let token = try await KeychainVault.shared.token() else {
                status = "Enroll this device first"
                return false
            }
            let client = CoordinatorClient(configuration: configuration)
            let battery = UIDevice.current.batteryLevel >= 0 ? Double(UIDevice.current.batteryLevel * 100) : nil
            let command = try await client.heartbeat(
                token: token,
                batteryPercent: battery,
                onBattery: UIDevice.current.batteryState == .unplugged
            )
            if command.action == "wipe" {
                await wipe(command: command, client: client)
                return true
            }
            guard let lease = try await client.lease(token: token) else { return true }
            let renewal = Task {
                while !Task.isCancelled {
                    try await Task.sleep(for: .seconds(45))
                    try await client.renew(token: token, leaseId: lease.leaseId)
                }
            }
            let clock = ContinuousClock()
            let started = clock.now
            do {
                let prompt = try LeasePromptBuilder.build(lease)
                let activeEngine = try engine ?? CoreMLTextEngine(compiledModelURL: ModelStore.shared.compiledModelURL)
                engine = activeEngine
                let output = try await activeEngine.complete(prompt: prompt, maximumTokens: 256)
                let elapsed = started.duration(to: clock.now)
                let durationMs = Int(elapsed.components.seconds * 1_000)
                    + Int(elapsed.components.attoseconds / 1_000_000_000_000_000)
                try await client.complete(token: token, leaseId: lease.leaseId, output: output, durationMs: durationMs)
                status = "Lease \(lease.leaseId.prefix(8))… completed"
            } catch {
                try? await client.fail(token: token, leaseId: lease.leaseId, error: error.localizedDescription)
                status = "Lease failed: \(error.localizedDescription)"
            }
            renewal.cancel()
            return true
        } catch is CancellationError {
            return false
        } catch {
            status = "Coordinator unavailable: \(error.localizedDescription)"
            return false
        }
    }

    private func wipe(command: ControlCommand, client: CoordinatorClient) async {
        credentialWiped = true
        running = false
        engine = nil
        do {
            let report = try await RemoteWipeExecutor.execute(
                command: command,
                vault: KeychainVault.shared,
                managedRoot: ModelStore.shared.managedRoot,
                managedDirectories: [ModelStore.shared.modelsDirectory],
                clearConfiguration: {
                    await ConfigurationStore.shared.clear()
                    await AppAttestor.shared.wipeKeyReference()
                },
                acknowledge: { token, generation, localDeleted in
                    try await client.acknowledgeWipe(token: token, generation: generation, localDataDeleted: localDeleted)
                }
            )
            modelReady = false
            status = report.acknowledged
                ? "Remote wipe complete and acknowledged"
                : "Credentials wiped locally; acknowledgement pending"
        } catch {
            status = "Remote wipe failed locally: \(error.localizedDescription)"
        }
    }

    private func currentConfiguration() throws -> EdgeConfiguration {
        guard let url = URL(string: coordinatorURL) else { throw EdgeConfigurationError.invalidCoordinatorURL }
        return try EdgeConfiguration(coordinatorURL: url, nodeName: nodeName, modelName: modelName)
    }

    private func validApplicationId(_ value: String) -> Bool {
        value.count <= 255 && value.range(of: #"^[A-Z0-9]{10}\.[A-Za-z0-9.-]+$"#, options: .regularExpression) != nil
    }
}
