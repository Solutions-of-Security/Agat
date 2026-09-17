import Foundation

public protocol TextInferenceEngine: Sendable {
    func complete(prompt: String, maximumTokens: Int) async throws -> String
}

public enum TextInferenceError: Error, Equatable {
    case unavailable
    case modelContractMismatch
    case invalidMaximumTokens
    case outputTooLarge
}

#if canImport(CoreML) && canImport(Metal)
@preconcurrency import CoreML
import Metal

public actor CoreMLTextEngine: TextInferenceEngine {
    private let model: MLModel
    private var predictionInFlight = false
    public let metalDeviceName: String

    public init(compiledModelURL: URL) throws {
        guard let device = MTLCreateSystemDefaultDevice() else { throw TextInferenceError.unavailable }
        let configuration = MLModelConfiguration()
        configuration.computeUnits = .all
        self.model = try MLModel(contentsOf: compiledModelURL, configuration: configuration)
        self.metalDeviceName = device.name
        guard model.modelDescription.inputDescriptionsByName["prompt"]?.type == .string,
              model.modelDescription.inputDescriptionsByName["max_tokens"]?.type == .int64,
              model.modelDescription.outputDescriptionsByName["text"]?.type == .string else {
            throw TextInferenceError.modelContractMismatch
        }
    }

    public func complete(prompt: String, maximumTokens: Int) async throws -> String {
        guard 1...512 ~= maximumTokens else { throw TextInferenceError.invalidMaximumTokens }
        guard !predictionInFlight else { throw TextInferenceError.unavailable }
        predictionInFlight = true
        defer { predictionInFlight = false }
        let input = try MLDictionaryFeatureProvider(dictionary: [
            "prompt": MLFeatureValue(string: prompt),
            "max_tokens": MLFeatureValue(int64: Int64(maximumTokens)),
        ])
        // Core ML's async API predates complete Sendable annotations. The
        // explicit guard keeps this reentrant actor single-flight while
        // @preconcurrency limits the compatibility relaxation to Core ML.
        let output = try await model.prediction(from: input)
        guard let text = output.featureValue(for: "text")?.stringValue else {
            throw TextInferenceError.modelContractMismatch
        }
        guard text.utf8.count <= 1_000_000 else { throw TextInferenceError.outputTooLarge }
        return text
    }
}
#endif
