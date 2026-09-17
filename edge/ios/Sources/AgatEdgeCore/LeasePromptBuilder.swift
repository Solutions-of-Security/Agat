import Foundation

public enum LeasePromptError: Error, Equatable {
    case unsupportedRuntime
    case toolsNotAllowed
    case activityNotAllowed
    case tooLarge
}

public enum LeasePromptBuilder {
    public static func build(_ lease: EdgeLease, maximumUTF8Bytes: Int = 48_000) throws -> String {
        guard lease.agent.runtime == "single" else { throw LeasePromptError.unsupportedRuntime }
        guard lease.mcpTools.isEmpty else { throw LeasePromptError.toolsNotAllowed }
        guard lease.activity == nil else { throw LeasePromptError.activityNotAllowed }
        var prompt = "<|system|>\n\(lease.agent.systemPrompt)\n<|context|>\n"
        for item in lease.context {
            prompt += "\(item.agentName): \(item.output)\n"
        }
        prompt += "<|user|>\n\(lease.run.input)\n<|assistant|>\n"
        guard prompt.utf8.count <= maximumUTF8Bytes else { throw LeasePromptError.tooLarge }
        return prompt
    }
}
