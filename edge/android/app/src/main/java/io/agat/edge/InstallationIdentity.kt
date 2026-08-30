package io.agat.edge

import android.os.Build
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyInfo
import android.security.keystore.KeyProperties
import android.security.keystore.StrongBoxUnavailableException
import android.util.Base64
import java.security.KeyFactory
import java.security.KeyPairGenerator
import java.security.KeyStore
import java.security.PrivateKey
import java.security.spec.ECGenParameterSpec
import java.security.MessageDigest

data class InstallationKey(val keyId: String, val hardwareBacked: Boolean, val strongBoxBacked: Boolean)

object InstallationIdentity {
    private const val KEY_ALIAS = "agat-edge-installation-v1"

    fun loadOrCreate(): InstallationKey {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (!keyStore.containsAlias(KEY_ALIAS)) generate(strongBox = Build.VERSION.SDK_INT >= 28)
        val certificate = keyStore.getCertificate(KEY_ALIAS) ?: error("Installation certificate отсутствует")
        val privateKey = keyStore.getKey(KEY_ALIAS, null) as PrivateKey
        val keyInfo = KeyFactory.getInstance(privateKey.algorithm, "AndroidKeyStore")
            .getKeySpec(privateKey, KeyInfo::class.java)
        val keyId = Base64.encodeToString(
            MessageDigest.getInstance("SHA-256").digest(certificate.publicKey.encoded),
            Base64.URL_SAFE or Base64.NO_WRAP or Base64.NO_PADDING,
        )
        val securityLevel = if (Build.VERSION.SDK_INT >= 31) keyInfo.securityLevel else null
        val hardwareBacked = securityLevel == KeyProperties.SECURITY_LEVEL_TRUSTED_ENVIRONMENT
            || securityLevel == KeyProperties.SECURITY_LEVEL_STRONGBOX
            || (securityLevel == null && legacyHardwareBacked(keyInfo))
        return InstallationKey(
            keyId,
            hardwareBacked,
            securityLevel == KeyProperties.SECURITY_LEVEL_STRONGBOX,
        )
    }

    fun wipe() {
        val keyStore = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        if (keyStore.containsAlias(KEY_ALIAS)) keyStore.deleteEntry(KEY_ALIAS)
    }

    private fun generate(strongBox: Boolean) {
        val spec = KeyGenParameterSpec.Builder(KEY_ALIAS, KeyProperties.PURPOSE_SIGN or KeyProperties.PURPOSE_VERIFY)
            .setAlgorithmParameterSpec(ECGenParameterSpec("secp256r1"))
            .setDigests(KeyProperties.DIGEST_SHA256)
            .setUserAuthenticationRequired(false)
            .apply { if (Build.VERSION.SDK_INT >= 28) setIsStrongBoxBacked(strongBox) }
            .build()
        try {
            KeyPairGenerator.getInstance(KeyProperties.KEY_ALGORITHM_EC, "AndroidKeyStore").apply { initialize(spec) }.generateKeyPair()
        } catch (error: StrongBoxUnavailableException) {
            if (!strongBox) throw error
            generate(strongBox = false)
        }
    }

    @Suppress("DEPRECATION")
    private fun legacyHardwareBacked(keyInfo: KeyInfo): Boolean = keyInfo.isInsideSecureHardware
}
