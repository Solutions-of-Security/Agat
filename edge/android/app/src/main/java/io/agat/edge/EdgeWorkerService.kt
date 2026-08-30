package io.agat.edge

import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.app.Service
import android.content.Intent
import android.os.BatteryManager
import android.os.IBinder
import androidx.core.app.NotificationCompat
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.SupervisorJob
import kotlinx.coroutines.cancel
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.delay
import kotlinx.coroutines.isActive
import kotlinx.coroutines.launch
import org.json.JSONObject
import java.io.File

class EdgeWorkerService : Service() {
    private val scope = CoroutineScope(SupervisorJob() + Dispatchers.IO)
    private var workerJob: Job? = null
    private var engine: NativeLlama? = null

    override fun onCreate() {
        super.onCreate()
        createNotificationChannel()
        val open = PendingIntent.getActivity(
            this,
            0,
            Intent(this, MainActivity::class.java),
            PendingIntent.FLAG_IMMUTABLE or PendingIntent.FLAG_UPDATE_CURRENT,
        )
        val notification = NotificationCompat.Builder(this, CHANNEL_ID)
            .setSmallIcon(android.R.drawable.stat_notify_sync)
            .setContentTitle(getString(R.string.app_name))
            .setContentText(getString(R.string.worker_running))
            .setOngoing(true)
            .setContentIntent(open)
            .build()
        startForeground(NOTIFICATION_ID, notification)
    }

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        if (workerJob?.isActive != true) workerJob = scope.launch { runWorker() }
        return START_NOT_STICKY
    }

    override fun onBind(intent: Intent?): IBinder? = null

    override fun onDestroy() {
        engine?.cancel()
        engine?.close()
        engine = null
        scope.cancel()
        super.onDestroy()
    }

    private suspend fun runWorker() {
        val configuration = EdgeConfiguration.load(this) ?: return stopSelf()
        val vault = CredentialVault(this)
        val token = vault.nodeToken() ?: return stopSelf()
        val client = CoordinatorClient(configuration)
        var backoffMs = 2_000L
        while (scope.isActive) {
            try {
                val command = client.heartbeat(token, batteryPercent(), onBattery())
                if (command.action == "wipe") {
                    executeWipe(client, vault, token, command)
                    return
                }
                val lease = client.lease(token)
                if (lease == null) {
                    backoffMs = 2_000L
                    delay(2_000)
                    continue
                }
                val pendingWipe = executeLease(client, token, configuration, lease)
                if (pendingWipe != null) {
                    executeWipe(client, vault, token, pendingWipe)
                    return
                }
                backoffMs = 2_000L
            } catch (cancelled: CancellationException) {
                throw cancelled
            } catch (_: Exception) {
                delay(backoffMs)
                backoffMs = (backoffMs * 2).coerceAtMost(30_000L)
            }
        }
    }

    private suspend fun executeLease(
        client: CoordinatorClient,
        token: String,
        configuration: EdgeConfiguration,
        lease: JSONObject,
    ): ControlCommand? {
        val leaseId = lease.getString("leaseId")
        val renew = scope.launch {
            while (isActive) {
                delay(45_000)
                client.renew(token, leaseId)
            }
        }
        val startedAt = System.nanoTime()
        var controlMonitor: Job? = null
        val wipeSignal = CompletableDeferred<ControlCommand>()
        try {
            require(!lease.has("activity")) { "HTTP/process activities запрещены на native edge worker" }
            require(lease.optJSONArray("mcpTools")?.length() == 0) { "MCP tools запрещены на native edge worker" }
            val agent = lease.getJSONObject("agent")
            require(agent.optString("runtime") == "single") { "Native edge поддерживает только single runtime" }
            val modelFile = configuration.modelFile(this)
            require(BuildConfig.LLAMA_RUNTIME_PACKAGED) { "llama.cpp runtime не включён в сборку" }
            require(modelFile.isFile && modelFile.length() > 0) { "GGUF model отсутствует в app-managed storage" }
            if (configuration.backend == InferenceBackend.VULKAN) require(NativeLlama.hasVulkan()) { "Vulkan backend недоступен" }
            val activeEngine = engine ?: NativeLlama(modelFile.absolutePath, configuration.backend).also { engine = it }
            controlMonitor = scope.launch {
                while (isActive) {
                    delay(5_000)
                    val command = runCatching { client.control(token) }.getOrNull() ?: continue
                    if (command.action == "wipe") {
                        wipeSignal.complete(command)
                        activeEngine.cancel()
                        break
                    }
                }
            }
            val output = activeEngine.complete(prompt(lease), 256)
            val durationMs = (System.nanoTime() - startedAt) / 1_000_000
            client.complete(token, leaseId, output, durationMs)
        } catch (error: Exception) {
            if (!wipeSignal.isCompleted) {
                runCatching { client.fail(token, leaseId, error.message ?: "Native inference failed") }
            }
        } finally {
            renew.cancel()
            controlMonitor?.cancelAndJoin()
        }
        return if (wipeSignal.isCompleted) wipeSignal.await() else null
    }

    private fun prompt(lease: JSONObject): String {
        val agent = lease.getJSONObject("agent")
        val run = lease.getJSONObject("run")
        val builder = StringBuilder()
            .append("<|system|>\n")
            .append(agent.getString("systemPrompt"))
            .append("\n<|context|>\n")
        lease.optJSONArray("context")?.let { context ->
            for (index in 0 until context.length()) {
                val item = context.getJSONObject(index)
                builder.append(item.optString("agentName")).append(": ").append(item.optString("output")).append('\n')
            }
        }
        val memory = lease.optJSONObject("knowledge")?.optJSONArray("memory")
        if (memory != null) {
            for (index in 0 until memory.length()) builder.append("memory: ").append(memory.getJSONObject(index).optString("content")).append('\n')
        }
        builder.append("<|user|>\n").append(run.getString("input")).append("\n<|assistant|>\n")
        require(builder.length <= 48_000) { "Prepared prompt слишком большой" }
        return builder.toString()
    }

    private suspend fun executeWipe(
        client: CoordinatorClient,
        vault: CredentialVault,
        token: String,
        command: ControlCommand,
    ) {
        engine?.close()
        engine = null
        vault.wipe()
        InstallationIdentity.wipe()
        EdgeConfiguration.clear(this)
        val localDataDeleted = deleteManaged(File(filesDir, "models")) and deleteManaged(cacheDir)
        for (attempt in 0 until 3) {
            if (runCatching { client.acknowledgeWipe(token, command.generation, localDataDeleted) }.isSuccess) break
            delay((attempt + 1) * 1_000L)
        }
        stopSelf()
    }

    private fun deleteManaged(directory: File): Boolean = runCatching {
        if (!directory.exists()) true else directory.deleteRecursively()
    }.getOrDefault(false)

    private fun batteryPercent(): Double? {
        val manager = getSystemService(BATTERY_SERVICE) as BatteryManager
        val value = manager.getIntProperty(BatteryManager.BATTERY_PROPERTY_CAPACITY)
        return value.takeIf { it in 0..100 }?.toDouble()
    }

    private fun onBattery(): Boolean {
        val manager = getSystemService(BATTERY_SERVICE) as BatteryManager
        return !manager.isCharging
    }

    private fun createNotificationChannel() {
        val manager = getSystemService(NOTIFICATION_SERVICE) as NotificationManager
        manager.createNotificationChannel(NotificationChannel(CHANNEL_ID, getString(R.string.worker_channel), NotificationManager.IMPORTANCE_LOW))
    }

    companion object {
        private const val CHANNEL_ID = "agat-edge-worker-v1"
        private const val NOTIFICATION_ID = 160
    }
}
