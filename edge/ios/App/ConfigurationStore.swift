import Foundation
import AgatEdgeCore

actor ConfigurationStore {
    static let shared = ConfigurationStore()
    private let key = "agat.edge.configuration.v1"
    private let appIdKey = "agat.edge.application-id.v1"

    func save(_ configuration: EdgeConfiguration, applicationId: String) throws {
        UserDefaults.standard.set(try JSONEncoder().encode(configuration), forKey: key)
        UserDefaults.standard.set(applicationId, forKey: appIdKey)
    }

    func load() throws -> (EdgeConfiguration, String)? {
        guard let data = UserDefaults.standard.data(forKey: key),
              let applicationId = UserDefaults.standard.string(forKey: appIdKey) else { return nil }
        return (try JSONDecoder().decode(EdgeConfiguration.self, from: data), applicationId)
    }

    func clear() {
        UserDefaults.standard.removeObject(forKey: key)
        UserDefaults.standard.removeObject(forKey: appIdKey)
    }
}
