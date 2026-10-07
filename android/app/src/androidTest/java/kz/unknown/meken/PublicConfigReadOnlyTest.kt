package kz.unknown.meken

import androidx.test.ext.junit.runners.AndroidJUnit4
import androidx.test.platform.app.InstrumentationRegistry
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.TokenPair
import kz.unknown.meken.data.ApiClient
import kz.unknown.meken.data.SessionStorage
import org.junit.Assert.assertTrue
import org.junit.Assume.assumeTrue
import org.junit.Test
import org.junit.runner.RunWith

/** Opt-in GET only: prove Android system TLS and the real production config.
 * No Activity launch, anonymous enrollment, token storage or business writes.
 */
@RunWith(AndroidJUnit4::class)
class PublicConfigReadOnlyTest {
    @Test fun productionConfigUsesSystemTrustWithoutEnrollment() = runBlocking {
        assumeTrue(InstrumentationRegistry.getArguments().getString("publicReadOnlySmoke") == "true")
        val forbiddenSession = object : SessionStorage {
            override fun load(): TokenPair? = error("Public config must not load a session")
            override fun save(tokens: TokenPair) = error("Public config must not save a session")
            override fun clear() = error("Public config must not clear a session")
            override fun isExpired(): Boolean = error("Public config must not inspect a session")
            override fun markExpired() = error("Public config must not mutate a session")
            override fun clearExpired() = error("Public config must not mutate a session")
        }
        val api = ApiClient("https://194.238.43.134", forbiddenSession)
        val config = withTimeout(20_000) { api.call<AppConfig>("v1/config", authenticated = false) }
        assertTrue(config.mode == "live")
        assertTrue(config.cities.contains("Астана"))
    }
}
