import CoreML
import Foundation

enum ModelStoreError: Error { case unsupportedFile }

struct ModelStore: Sendable {
    static let shared = ModelStore()

    var managedRoot: URL {
        URL.applicationSupportDirectory.appending(path: "AgatEdge", directoryHint: .isDirectory)
    }
    var modelsDirectory: URL { managedRoot.appending(path: "models", directoryHint: .isDirectory) }
    var compiledModelURL: URL { modelsDirectory.appending(path: "EdgeText.mlmodelc", directoryHint: .isDirectory) }

    func importModel(from source: URL) throws {
        let accessed = source.startAccessingSecurityScopedResource()
        defer { if accessed { source.stopAccessingSecurityScopedResource() } }
        let compiled: URL
        if source.pathExtension == "mlmodel" || source.pathExtension == "mlpackage" {
            compiled = try MLModel.compileModel(at: source)
        } else if source.pathExtension == "mlmodelc" {
            compiled = source
        } else {
            throw ModelStoreError.unsupportedFile
        }
        try FileManager.default.createDirectory(at: modelsDirectory, withIntermediateDirectories: true)
        if FileManager.default.fileExists(atPath: compiledModelURL.path) {
            try FileManager.default.removeItem(at: compiledModelURL)
        }
        try FileManager.default.copyItem(at: compiled, to: compiledModelURL)
    }

    func sizeBytes() -> Int64 {
        guard let enumerator = FileManager.default.enumerator(
            at: compiledModelURL,
            includingPropertiesForKeys: [.fileSizeKey],
            options: [.skipsHiddenFiles]
        ) else { return 0 }
        return enumerator.reduce(into: Int64(0)) { total, item in
            guard let url = item as? URL,
                  let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize else { return }
            total += Int64(size)
        }
    }
}
