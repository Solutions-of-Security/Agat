import SwiftUI
import UniformTypeIdentifiers

final class AppDelegate: NSObject, UIApplicationDelegate {
    func application(
        _ application: UIApplication,
        didFinishLaunchingWithOptions launchOptions: [UIApplication.LaunchOptionsKey: Any]? = nil
    ) -> Bool {
        BackgroundWorker.register()
        return true
    }

    func applicationDidEnterBackground(_ application: UIApplication) {
        BackgroundWorker.schedule()
    }
}

@main
struct AgatEdgeApp: App {
    @UIApplicationDelegateAdaptor(AppDelegate.self) private var delegate

    var body: some Scene {
        WindowGroup { EdgeWorkerView(controller: .shared) }
    }
}

struct EdgeWorkerView: View {
    @ObservedObject var controller: WorkerController
    @State private var bootstrapToken = ""
    @State private var importing = false

    var body: some View {
        NavigationStack {
            Form {
                Section("Coordinator") {
                    TextField("https://coordinator.example", text: $controller.coordinatorURL)
                        .textInputAutocapitalization(.never)
                        .keyboardType(.URL)
                    TextField("Node name", text: $controller.nodeName)
                    TextField("TEAMID.io.agat.edge", text: $controller.applicationId)
                        .textInputAutocapitalization(.characters)
                    SecureField("One-time enrollment token", text: $bootstrapToken)
                }
                Section("Core ML / Metal") {
                    TextField("Coordinator model name", text: $controller.modelName)
                    Button(controller.modelReady ? "Replace managed model" : "Import .mlmodel/.mlpackage/.mlmodelc") {
                        importing = true
                    }
                }
                Section("Trust") {
                    Button("App Attest & enroll") {
                        let token = bootstrapToken
                        bootstrapToken = ""
                        Task { await controller.enroll(bootstrapToken: token) }
                    }
                    .disabled(!controller.modelReady)
                }
                Section("Worker") {
                    Button(controller.running ? "Stop" : "Start while app is active") {
                        controller.running ? controller.stop() : controller.startForeground()
                    }
                    Text("Background processing is opportunistic: iOS chooses if and when BGProcessingTask runs.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                Section("Status") {
                    Text(controller.status).font(.system(.footnote, design: .monospaced))
                }
            }
            .navigationTitle("АГАТ Edge")
            .fileImporter(isPresented: $importing, allowedContentTypes: [.data]) { result in
                if case let .success(url) = result { Task { await controller.importModel(url) } }
            }
        }
    }
}
