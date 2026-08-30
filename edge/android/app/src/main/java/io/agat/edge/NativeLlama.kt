package io.agat.edge

import java.io.Closeable

class NativeLlama(
    modelPath: String,
    backend: InferenceBackend,
    contextTokens: Int = 4_096,
) : Closeable {
    @Volatile private var handle = nativeCreate(modelPath, backend.ordinal, contextTokens)

    init { check(handle != 0L) { "llama.cpp model не загружен" } }

    @Synchronized
    fun complete(prompt: String, maxTokens: Int = 256): String {
        check(handle != 0L) { "Inference engine уже закрыт" }
        require(prompt.toByteArray(Charsets.UTF_8).size <= 64 * 1024) { "Prompt слишком большой для edge worker" }
        require(maxTokens in 1..512) { "maxTokens должен быть 1..512" }
        return nativeComplete(handle, prompt, maxTokens)
    }

    fun cancel() {
        val active = handle
        if (active != 0L) nativeCancel(active)
    }

    @Synchronized
    override fun close() {
        if (handle != 0L) nativeDestroy(handle)
        handle = 0L
    }

    private external fun nativeCreate(modelPath: String, backend: Int, contextTokens: Int): Long
    private external fun nativeComplete(handle: Long, prompt: String, maxTokens: Int): String
    private external fun nativeCancel(handle: Long)
    private external fun nativeDestroy(handle: Long)

    companion object {
        init { System.loadLibrary("agat_llama") }

        private external fun nativeHasVulkan(): Boolean
        private external fun nativeNnapiFeatureLevel(): Long

        fun hasVulkan(): Boolean = runCatching { nativeHasVulkan() }.getOrDefault(false)

        fun nnapiCapabilityLabel(): String {
            val level = runCatching { nativeNnapiFeatureLevel() }.getOrDefault(0)
            return when {
                level <= 0 -> "unavailable"
                android.os.Build.VERSION.SDK_INT >= 35 -> "deprecated-feature-$level"
                else -> "legacy-feature-$level"
            }
        }
    }
}
