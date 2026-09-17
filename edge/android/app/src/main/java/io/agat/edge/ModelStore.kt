package io.agat.edge

import android.content.Context
import android.net.Uri
import java.io.File
import java.io.IOException
import java.nio.file.Files
import java.nio.file.AtomicMoveNotSupportedException
import java.nio.file.StandardCopyOption
import java.util.UUID

object ModelStore {
    private const val MAX_MODEL_BYTES = 64L * 1024 * 1024 * 1024

    fun importGguf(context: Context, source: Uri): Long {
        require(source.scheme == "content") { "Выберите GGUF через системный document picker" }
        val directory = File(context.filesDir, "models")
        check(directory.exists() || directory.mkdirs()) { "Не удалось создать managed model directory" }
        val target = File(directory, "model.gguf")
        val temporary = File(directory, ".import-${UUID.randomUUID()}.tmp")
        var total = 0L
        try {
            val input = context.contentResolver.openInputStream(source) ?: throw IOException("Не удалось открыть GGUF")
            input.use { stream ->
                temporary.outputStream().buffered().use { output ->
                    val buffer = ByteArray(1024 * 1024)
                    while (true) {
                        val count = stream.read(buffer)
                        if (count < 0) break
                        total += count
                        if (total > MAX_MODEL_BYTES) throw IOException("GGUF превышает лимит 64 GiB")
                        output.write(buffer, 0, count)
                    }
                }
            }
            require(total > 0) { "GGUF пуст" }
            try {
                Files.move(
                    temporary.toPath(),
                    target.toPath(),
                    StandardCopyOption.ATOMIC_MOVE,
                    StandardCopyOption.REPLACE_EXISTING,
                )
            } catch (_: AtomicMoveNotSupportedException) {
                Files.move(temporary.toPath(), target.toPath(), StandardCopyOption.REPLACE_EXISTING)
            }
            return total
        } finally {
            if (temporary.exists()) temporary.delete()
        }
    }
}
