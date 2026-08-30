package io.agat.edge

import android.content.Context
import java.io.File
import java.net.URI

data class EdgeConfiguration(
    val coordinatorUrl: String,
    val nodeName: String,
    val modelName: String,
    val backend: InferenceBackend,
) {
    val modelFileName: String = "model.gguf"

    fun modelFile(context: Context): File = File(File(context.filesDir, "models"), modelFileName)

    fun validate(): EdgeConfiguration {
        val uri = URI(coordinatorUrl.trim().trimEnd('/'))
        val scheme = uri.scheme?.lowercase()
        require(!uri.host.isNullOrBlank() && (uri.path.isNullOrEmpty() || uri.path == "/")) {
            "Coordinator URL должен содержать только origin"
        }
        require(scheme == "https" || (scheme == "http" && uri.host.lowercase() in LOOPBACK_HOSTS)) {
            "Coordinator должен использовать HTTPS; HTTP разрешён только для loopback/emulator"
        }
        require(uri.userInfo == null && uri.fragment == null && uri.query == null) { "Некорректный URL coordinator" }
        require(nodeName.isNotBlank() && nodeName.length <= 120 && !nodeName.contains(Regex("[\\r\\n\\u0000]"))) {
            "Некорректное имя узла"
        }
        require(modelName.isNotBlank() && modelName.length <= 200 && !modelName.contains(Regex("[\\r\\n\\u0000]"))) {
            "Некорректное имя модели"
        }
        return copy(coordinatorUrl = coordinatorUrl.trim().trimEnd('/'), nodeName = nodeName.trim(), modelName = modelName.trim())
    }

    fun save(context: Context) {
        validate()
        context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit()
            .putString("coordinator_url", coordinatorUrl.trim().trimEnd('/'))
            .putString("node_name", nodeName.trim())
            .putString("model_name", modelName.trim())
            .putString("backend", backend.name)
            .apply()
    }

    companion object {
        private const val PREFERENCES = "agat_edge_config_v1"
        private val LOOPBACK_HOSTS = setOf("127.0.0.1", "localhost", "10.0.2.2")

        fun load(context: Context): EdgeConfiguration? {
            val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)
            val url = preferences.getString("coordinator_url", null) ?: return null
            val name = preferences.getString("node_name", null) ?: return null
            val model = preferences.getString("model_name", null) ?: return null
            val backend = runCatching {
                InferenceBackend.valueOf(preferences.getString("backend", InferenceBackend.VULKAN.name)!!)
            }.getOrDefault(InferenceBackend.CPU)
            return runCatching { EdgeConfiguration(url, name, model, backend).validate() }.getOrNull()
        }

        fun clear(context: Context) {
            context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE).edit().clear().apply()
        }
    }
}

enum class InferenceBackend { VULKAN, CPU }
