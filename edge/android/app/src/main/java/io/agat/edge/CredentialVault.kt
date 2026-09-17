package io.agat.edge

import android.content.Context
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

class CredentialVault(private val context: Context) {
    private val preferences = context.getSharedPreferences(PREFERENCES, Context.MODE_PRIVATE)

    fun saveNodeToken(token: String) {
        require(token.length in 32..512 && !token.contains(Regex("[\\r\\n\\u0000]"))) { "Некорректный node token" }
        val cipher = Cipher.getInstance(TRANSFORMATION)
        cipher.init(Cipher.ENCRYPT_MODE, key())
        val encrypted = cipher.doFinal(token.toByteArray(Charsets.UTF_8))
        preferences.edit()
            .putString("token", encrypted.encodeBase64())
            .putString("iv", cipher.iv.encodeBase64())
            .apply()
    }

    fun nodeToken(): String? {
        val encrypted = preferences.getString("token", null)?.decodeBase64() ?: return null
        val iv = preferences.getString("iv", null)?.decodeBase64() ?: return null
        return runCatching {
            val cipher = Cipher.getInstance(TRANSFORMATION)
            cipher.init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, iv))
            String(cipher.doFinal(encrypted), Charsets.UTF_8)
        }.getOrNull()
    }

    fun wipe() {
        preferences.edit().clear().commit()
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
    }

    private fun key(): SecretKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (keyStore.getKey(KEY_ALIAS, null) as? SecretKey)?.let { return it }
        val generator = KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore")
        generator.init(
            KeyGenParameterSpec.Builder(
                KEY_ALIAS,
                KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT,
            )
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM)
                .setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE)
                .setKeySize(256)
                .setUserAuthenticationRequired(false)
                .build(),
        )
        return generator.generateKey()
    }

    private fun ByteArray.encodeBase64(): String = android.util.Base64.encodeToString(this, android.util.Base64.NO_WRAP)
    private fun String.decodeBase64(): ByteArray = android.util.Base64.decode(this, android.util.Base64.NO_WRAP)

    companion object {
        private const val PREFERENCES = "agat_edge_credentials_v1"
        private const val KEY_ALIAS = "agat-edge-node-token-v1"
        private const val TRANSFORMATION = "AES/GCM/NoPadding"
    }
}
