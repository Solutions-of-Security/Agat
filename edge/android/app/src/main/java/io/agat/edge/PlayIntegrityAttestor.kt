package io.agat.edge

import android.content.Context
import com.google.android.play.core.integrity.IntegrityManagerFactory
import com.google.android.play.core.integrity.StandardIntegrityManager
import kotlinx.coroutines.tasks.await

class PlayIntegrityAttestor(context: Context) {
    private val manager = IntegrityManagerFactory.createStandard(context.applicationContext)
    @Volatile private var provider: StandardIntegrityManager.StandardIntegrityTokenProvider? = null

    suspend fun warmUp() {
        require(BuildConfig.PLAY_INTEGRITY_CLOUD_PROJECT > 0) {
            "agatCloudProjectNumber должен быть задан для Play Integrity"
        }
        provider = manager.prepareIntegrityToken(
            StandardIntegrityManager.PrepareIntegrityTokenRequest.builder()
                .setCloudProjectNumber(BuildConfig.PLAY_INTEGRITY_CLOUD_PROJECT)
                .build(),
        ).await()
    }

    suspend fun token(requestHash: String): String {
        if (provider == null) warmUp()
        val active = checkNotNull(provider)
        return try {
            active.request(
                StandardIntegrityManager.StandardIntegrityTokenRequest.builder()
                    .setRequestHash(requestHash)
                    .build(),
            ).await().token()
        } catch (first: Exception) {
            provider = null
            warmUp()
            checkNotNull(provider).request(
                StandardIntegrityManager.StandardIntegrityTokenRequest.builder()
                    .setRequestHash(requestHash)
                    .build(),
            ).await().token()
        }
    }
}
