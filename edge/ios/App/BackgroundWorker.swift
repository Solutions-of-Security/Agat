import BackgroundTasks
import Foundation

private final class BackgroundProcessingHandle: @unchecked Sendable {
    private let task: BGProcessingTask

    init(_ task: BGProcessingTask) {
        self.task = task
    }

    func complete(success: Bool) {
        task.setTaskCompleted(success: success)
    }
}

enum BackgroundWorker {
    static let identifier = "io.agat.edge.processing"

    static func register() {
        BGTaskScheduler.shared.register(forTaskWithIdentifier: identifier, using: nil) { task in
            guard let processing = task as? BGProcessingTask else {
                task.setTaskCompleted(success: false)
                return
            }
            let handle = BackgroundProcessingHandle(processing)
            let operation = Task { @MainActor in
                let success = await WorkerController.shared.processOnce()
                handle.complete(success: success)
                schedule()
            }
            processing.expirationHandler = { operation.cancel() }
        }
    }

    static func schedule() {
        let request = BGProcessingTaskRequest(identifier: identifier)
        request.requiresNetworkConnectivity = true
        request.requiresExternalPower = true
        request.earliestBeginDate = Date(timeIntervalSinceNow: 15 * 60)
        try? BGTaskScheduler.shared.submit(request)
    }
}
