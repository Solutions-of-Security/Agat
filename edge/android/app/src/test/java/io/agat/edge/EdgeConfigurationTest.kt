package io.agat.edge

import org.junit.Assert.assertEquals
import org.junit.Assert.assertThrows
import org.junit.Test

class EdgeConfigurationTest {
    @Test
    fun remoteCleartextAndCoordinatorPathAreRejected() {
        assertThrows(IllegalArgumentException::class.java) {
            EdgeConfiguration("http://coordinator.example", "phone", "edge", InferenceBackend.CPU).validate()
        }
        assertThrows(IllegalArgumentException::class.java) {
            EdgeConfiguration("https://coordinator.example/base", "phone", "edge", InferenceBackend.CPU).validate()
        }
    }

    @Test
    fun loopbackAndEmulatorDevelopmentOriginsAreAccepted() {
        assertEquals(
            "http://10.0.2.2:8787",
            EdgeConfiguration("http://10.0.2.2:8787/", " phone ", " edge ", InferenceBackend.CPU)
                .validate().coordinatorUrl,
        )
    }
}
