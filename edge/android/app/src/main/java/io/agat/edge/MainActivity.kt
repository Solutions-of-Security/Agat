package io.agat.edge

import android.Manifest
import android.app.ActivityManager
import android.content.Intent
import android.content.pm.PackageManager
import android.graphics.Typeface
import android.os.Build
import android.os.Bundle
import android.text.InputType
import android.view.ViewGroup
import android.widget.Button
import android.widget.EditText
import android.widget.LinearLayout
import android.widget.RadioButton
import android.widget.RadioGroup
import android.widget.ScrollView
import android.widget.TextView
import androidx.activity.ComponentActivity
import androidx.activity.result.contract.ActivityResultContracts
import androidx.core.app.ActivityCompat
import androidx.core.content.ContextCompat
import androidx.lifecycle.lifecycleScope
import kotlinx.coroutines.launch

class MainActivity : ComponentActivity() {
    private lateinit var coordinator: EditText
    private lateinit var bootstrap: EditText
    private lateinit var nodeName: EditText
    private lateinit var modelName: EditText
    private lateinit var backend: RadioGroup
    private lateinit var status: TextView
    private val importModel = registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri ->
        if (uri == null) return@registerForActivityResult
        lifecycleScope.launch(kotlinx.coroutines.Dispatchers.IO) {
            runCatching { ModelStore.importGguf(this@MainActivity, uri) }
                .onSuccess { bytes -> runOnUiThread { renderStatus("GGUF imported: $bytes bytes") } }
                .onFailure { error -> runOnUiThread { renderStatus("Model import failed: ${error.message}") } }
        }
    }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        setContentView(content())
        EdgeConfiguration.load(this)?.let {
            coordinator.setText(it.coordinatorUrl)
            nodeName.setText(it.nodeName)
            modelName.setText(it.modelName)
            backend.check(if (it.backend == InferenceBackend.VULKAN) ID_VULKAN else ID_CPU)
        }
        renderStatus()
    }

    private fun content(): ScrollView {
        val density = resources.displayMetrics.density
        fun dp(value: Int) = (value * density).toInt()
        val root = LinearLayout(this).apply {
            orientation = LinearLayout.VERTICAL
            setPadding(dp(24), dp(28), dp(24), dp(32))
        }
        root.addView(TextView(this).apply {
            text = "АГАТ · Native edge worker"
            textSize = 22f
            setTypeface(typeface, Typeface.BOLD)
        })
        root.addView(TextView(this).apply {
            text = "Play Integrity enrollment, secure node credential и user-visible foreground inference."
            textSize = 14f
            setPadding(0, dp(8), 0, dp(20))
        })
        coordinator = field("https://coordinator.example", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_URI)
        bootstrap = field("One-time enrollment token", InputType.TYPE_CLASS_TEXT or InputType.TYPE_TEXT_VARIATION_PASSWORD)
        nodeName = field("Node name", InputType.TYPE_CLASS_TEXT)
        modelName = field("Coordinator model name", InputType.TYPE_CLASS_TEXT)
        listOf(coordinator, bootstrap, nodeName, modelName).forEach(root::addView)
        backend = RadioGroup(this).apply {
            orientation = RadioGroup.HORIZONTAL
            addView(RadioButton(this@MainActivity).apply { id = ID_VULKAN; text = "Vulkan" })
            addView(RadioButton(this@MainActivity).apply { id = ID_CPU; text = "CPU" })
            check(ID_VULKAN)
        }
        root.addView(backend)
        root.addView(Button(this).apply {
            text = "Import managed GGUF"
            setOnClickListener { importModel.launch(arrayOf("application/octet-stream", "*/*")) }
        })
        root.addView(Button(this).apply {
            text = "Attest & enroll"
            setOnClickListener { enroll() }
        })
        root.addView(Button(this).apply {
            text = "Start visible worker"
            setOnClickListener { startWorker() }
        })
        root.addView(Button(this).apply {
            text = "Stop worker"
            setOnClickListener { stopService(Intent(this@MainActivity, EdgeWorkerService::class.java)); renderStatus("Worker stopped") }
        })
        status = TextView(this).apply {
            setPadding(0, dp(18), 0, 0)
            typeface = Typeface.MONOSPACE
        }
        root.addView(status)
        return ScrollView(this).apply { addView(root, ViewGroup.LayoutParams(ViewGroup.LayoutParams.MATCH_PARENT, ViewGroup.LayoutParams.WRAP_CONTENT)) }
    }

    private fun field(hint: String, inputTypeValue: Int) = EditText(this).apply {
        this.hint = hint
        inputType = inputTypeValue
        setSingleLine(true)
    }

    private fun configuration(): EdgeConfiguration = EdgeConfiguration(
        coordinatorUrl = coordinator.text.toString(),
        nodeName = nodeName.text.toString(),
        modelName = modelName.text.toString(),
        backend = if (backend.checkedRadioButtonId == ID_CPU) InferenceBackend.CPU else InferenceBackend.VULKAN,
    ).validate()

    private fun enroll() {
        lifecycleScope.launch {
            setBusy(true, "Requesting one-time challenge…")
            runCatching {
                val config = configuration()
                require(config.modelFile(this@MainActivity).isFile) {
                    "Place a GGUF model at ${config.modelFile(this@MainActivity).absolutePath}"
                }
                require(BuildConfig.LLAMA_RUNTIME_PACKAGED) { "Build without pinned llama.cpp runtime is not enrollable" }
                val client = CoordinatorClient(config)
                val challenge = client.requestChallenge(bootstrap.text.toString())
                bootstrap.text.clear()
                val installation = InstallationIdentity.loadOrCreate()
                val requestHash = client.requestHash(client.attestationBinding(challenge, installation.keyId))
                val integrityToken = PlayIntegrityAttestor(this@MainActivity).token(requestHash)
                val memoryInfo = ActivityManager.MemoryInfo().also {
                    (getSystemService(ACTIVITY_SERVICE) as ActivityManager).getMemoryInfo(it)
                }
                val enrolled = client.enroll(
                    challenge,
                    integrityToken,
                    installation,
                    config.modelFile(this@MainActivity).length(),
                    memoryInfo.totalMem / (1024 * 1024),
                )
                CredentialVault(this@MainActivity).saveNodeToken(enrolled.token)
                config.save(this@MainActivity)
                "Enrolled ${enrolled.id.take(8)}… · hardware=${installation.hardwareBacked}"
            }.onSuccess { renderStatus(it) }
                .onFailure { renderStatus("Enrollment failed: ${it.message}") }
            setBusy(false)
        }
    }

    private fun startWorker() {
        runCatching {
            val config = configuration()
            require(CredentialVault(this).nodeToken() != null) { "Enroll this device first" }
            require(config.modelFile(this).isFile) { "Managed GGUF model is missing" }
            config.save(this)
            if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(this, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) {
                ActivityCompat.requestPermissions(this, arrayOf(Manifest.permission.POST_NOTIFICATIONS), 160)
            }
            ContextCompat.startForegroundService(this, Intent(this, EdgeWorkerService::class.java))
        }.onSuccess { renderStatus("Foreground worker requested") }
            .onFailure { renderStatus("Cannot start: ${it.message}") }
    }

    private fun setBusy(busy: Boolean, message: String? = null) {
        coordinator.isEnabled = !busy
        bootstrap.isEnabled = !busy
        nodeName.isEnabled = !busy
        modelName.isEnabled = !busy
        backend.isEnabled = !busy
        if (message != null) renderStatus(message)
    }

    private fun renderStatus(message: String? = null) {
        if (!::status.isInitialized) return
        status.text = message ?: buildString {
            append(if (CredentialVault(this@MainActivity).nodeToken() == null) "Not enrolled" else "Credential stored in Android Keystore")
            append("\nllama.cpp packaged: ").append(BuildConfig.LLAMA_RUNTIME_PACKAGED)
            append("\nVulkan: ").append(NativeLlama.hasVulkan())
            append("\nNNAPI: ").append(NativeLlama.nnapiCapabilityLabel())
        }
    }

    companion object {
        private const val ID_VULKAN = 1601
        private const val ID_CPU = 1602
    }
}
