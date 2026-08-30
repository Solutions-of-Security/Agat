package io.agat.edge

import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.IOException
import java.io.ByteArrayOutputStream
import java.net.HttpURLConnection
import java.net.URL
import java.security.MessageDigest
import android.util.Base64

data class EnrollmentChallenge(val id: String, val challenge: String, val expiresAt: String)
data class EnrolledNode(val id: String, val token: String)
data class ControlCommand(val action: String, val generation: Int, val reason: String?)

class CoordinatorClient(private val configuration: EdgeConfiguration) {
    private val baseUrl = configuration.validate().coordinatorUrl

    suspend fun requestChallenge(bootstrapToken: String): EnrollmentChallenge {
        val payload = JSONObject()
            .put("enrollmentToken", bootstrapToken)
            .put("name", configuration.nodeName)
            .put("platform", "android")
            .put("applicationId", BuildConfig.APPLICATION_ID)
        val response = request("POST", "/api/v1/edge/enrollment/challenges", payload)
        requireStatus(response, 201)
        val json = JSONObject(response.body)
        return EnrollmentChallenge(json.getString("id"), json.getString("challenge"), json.getString("expiresAt"))
    }

    suspend fun enroll(
        challenge: EnrollmentChallenge,
        attestationToken: String,
        installationKey: InstallationKey,
        modelSizeBytes: Long,
        memoryMb: Long,
    ): EnrolledNode {
        val capabilities = JSONArray().put("text")
        val modelProfiles = JSONArray().put(
            JSONObject()
                .put("name", configuration.modelName)
                .put("provider", "llama.cpp")
                .put("sizeBytes", modelSizeBytes)
                .put("quantization", "gguf")
                .put("capabilities", capabilities),
        )
        val payload = JSONObject()
            .put("challengeId", challenge.id)
            .put("challenge", challenge.challenge)
            .put("name", configuration.nodeName)
            .put("platform", "android")
            .put("architecture", android.os.Build.SUPPORTED_ABIS.firstOrNull() ?: "unknown")
            .put("models", JSONArray().put(configuration.modelName))
            .put("modelProfiles", modelProfiles)
            .put("embeddingModels", JSONArray())
            .put("maxConcurrency", 1)
            .put("memoryMb", memoryMb)
            .put("gpu", if (configuration.backend == InferenceBackend.VULKAN) "Vulkan" else "CPU")
            .put("labels", JSONObject()
                .put("runtime", "android-service")
                .put("inference", if (configuration.backend == InferenceBackend.VULKAN) "llama.cpp+vulkan" else "llama.cpp+cpu")
                .put("nnapi", NativeLlama.nnapiCapabilityLabel())
                .put("installationHardwareBacked", installationKey.hardwareBacked.toString())
                .put("strongBoxBacked", installationKey.strongBoxBacked.toString()))
            .put("attestation", JSONObject()
                .put("provider", "play_integrity")
                .put("token", attestationToken)
                .put("keyId", installationKey.keyId)
                .put("applicationId", BuildConfig.APPLICATION_ID))
        val response = request("POST", "/api/v1/edge/enroll", payload)
        requireStatus(response, 201)
        val json = JSONObject(response.body)
        return EnrolledNode(json.getString("id"), json.getString("token"))
    }

    suspend fun heartbeat(token: String, batteryPercent: Double?, onBattery: Boolean): ControlCommand {
        val payload = JSONObject()
            .put("metrics", JSONObject()
                .putOpt("batteryPercent", batteryPercent)
                .put("onBattery", onBattery))
            .put("capabilities", JSONObject()
                .put("models", JSONArray().put(configuration.modelName))
                .put("modelProfiles", JSONArray().put(JSONObject()
                    .put("name", configuration.modelName)
                    .put("provider", "llama.cpp")
                    .put("quantization", "gguf")
                    .put("capabilities", JSONArray().put("text"))))
                .put("embeddingModels", JSONArray())
                .put("agentRuntimes", JSONArray().put("single"))
                .put("agentRuntimeProfiles", JSONArray().put("tool_loop_v1"))
                .put("maxConcurrency", 1)
                .put("labels", JSONObject()
                    .put("runtime", "android-service")
                    .put("inference", if (configuration.backend == InferenceBackend.VULKAN) "llama.cpp+vulkan" else "llama.cpp+cpu")))
        val response = request("POST", "/api/v1/workers/heartbeat", payload, token)
        requireStatus(response, 200)
        return control(JSONObject(response.body))
    }

    suspend fun control(token: String): ControlCommand {
        val response = request("GET", "/api/v1/edge/control", null, token)
        requireStatus(response, 200)
        return control(JSONObject(response.body))
    }

    suspend fun lease(token: String): JSONObject? {
        val response = request(
            "POST",
            "/api/v1/workers/lease",
            JSONObject().put("workerVersion", "android/1.6.0"),
            token,
        )
        if (response.status == 204) return null
        requireStatus(response, 200)
        return JSONObject(response.body)
    }

    suspend fun renew(token: String, leaseId: String) {
        requireStatus(request("POST", "/api/v1/leases/${leaseId.pathSegment()}/renew", JSONObject(), token), 204)
    }

    suspend fun complete(token: String, leaseId: String, output: String, durationMs: Long) {
        val payload = JSONObject()
            .put("output", output)
            .put("metrics", JSONObject()
                .put("durationMs", durationMs)
                .put("modelCalls", 1)
                .put("model", configuration.modelName)
                .put("provider", if (configuration.backend == InferenceBackend.VULKAN) "llama.cpp-vulkan" else "llama.cpp-cpu"))
        requireStatus(request("POST", "/api/v1/leases/${leaseId.pathSegment()}/complete", payload, token), 200)
    }

    suspend fun fail(token: String, leaseId: String, message: String) {
        val safe = message.replace(Regex("[\\r\\n\\u0000]"), " ").take(2_000).ifBlank { "Native inference failed" }
        requireStatus(request("POST", "/api/v1/leases/${leaseId.pathSegment()}/fail", JSONObject().put("error", safe), token), 200)
    }

    suspend fun acknowledgeWipe(token: String, generation: Int, localDataDeleted: Boolean) {
        val payload = JSONObject()
            .put("generation", generation)
            .put("credentialsDeleted", true)
            .put("localDataDeleted", localDataDeleted)
        requireStatus(request("POST", "/api/v1/edge/control/wipe-ack", payload, token), 200)
    }

    fun attestationBinding(challenge: EnrollmentChallenge, keyId: String): String = listOf(
        "agat-edge-attestation-v1",
        challenge.id,
        challenge.challenge,
        configuration.nodeName,
        BuildConfig.APPLICATION_ID,
        keyId,
    ).joinToString("\n")

    fun requestHash(binding: String): String = Base64.encodeToString(
        MessageDigest.getInstance("SHA-256").digest(binding.toByteArray(Charsets.UTF_8)),
        Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
    )

    private fun control(json: JSONObject): ControlCommand = ControlCommand(
        action = json.getString("action"),
        generation = json.optInt("generation", 0),
        reason = json.optString("reason").takeIf { it.isNotBlank() && it != "null" },
    )

    private suspend fun request(method: String, path: String, body: JSONObject?, bearer: String? = null): HttpResponse =
        withContext(Dispatchers.IO) {
            val connection = URL("$baseUrl$path").openConnection() as HttpURLConnection
            try {
                connection.instanceFollowRedirects = false
                connection.requestMethod = method
                connection.connectTimeout = 10_000
                connection.readTimeout = 70_000
                connection.setRequestProperty("Accept", "application/json")
                connection.setRequestProperty("User-Agent", "agat-edge-android/1.6.0")
                bearer?.let { connection.setRequestProperty("Authorization", "Bearer $it") }
                if (body != null) {
                    connection.setRequestProperty("Content-Type", "application/json")
                    connection.doOutput = true
                    connection.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
                }
                val status = connection.responseCode
                val stream = if (status in 200..299) connection.inputStream else connection.errorStream
                val responseBody = stream?.use { input ->
                    val output = ByteArrayOutputStream()
                    val buffer = ByteArray(8_192)
                    var total = 0
                    while (true) {
                        val count = input.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > 1_048_576) throw IOException("Ответ coordinator слишком большой")
                        output.write(buffer, 0, count)
                    }
                    output.toString(Charsets.UTF_8.name())
                }.orEmpty()
                HttpResponse(status, responseBody)
            } finally {
                connection.disconnect()
            }
        }

    private fun requireStatus(response: HttpResponse, expected: Int) {
        if (response.status == expected) return
        val detail = runCatching { JSONObject(response.body).optString("error") }.getOrNull().orEmpty()
        throw IOException("Coordinator HTTP ${response.status}${if (detail.isBlank()) "" else ": ${detail.take(500)}"}")
    }

    private fun String.pathSegment(): String = java.net.URLEncoder.encode(this, Charsets.UTF_8.name()).replace("+", "%20")
    private data class HttpResponse(val status: Int, val body: String)
}
