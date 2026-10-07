package kz.unknown.meken.data

import kz.unknown.meken.core.TokenPair
import org.junit.Assert.*
import org.junit.Test

class TokenVaultTest {
    private val old = TokenPair("a".repeat(43), "r".repeat(64), 3600, "user")
    private val recovered = TokenPair("b".repeat(43), "s".repeat(64), 3600, "user")

    @Test fun `old expiry cannot reject a recovered pair after process death before marker cleanup`() {
        val records = MemoryRecords()
        val vault = TokenVault(records)
        vault.save(old)
        vault.markExpired()
        assertTrue(vault.isExpired())
        vault.save(recovered)
        // Simulate death before clearExpired(), then a newly constructed vault reading the same files.
        val restarted = TokenVault(records)
        assertEquals(recovered, restarted.load())
        assertFalse(restarted.isExpired())
    }

    @Test fun `legacy boolean expiry binds to its old pair before an atomic recovery replacement`() {
        val records = MemoryRecords()
        val vault = TokenVault(records)
        vault.save(old)
        records.write("session-expired", "true")
        // Recovery can arrive before bootstrap migrates a legacy marker.
        vault.save(recovered)
        assertEquals(recovered, TokenVault(records).load())
        assertFalse(TokenVault(records).isExpired())
    }

    private class MemoryRecords : PrivateRecords {
        private val data = mutableMapOf<String, String>()
        override fun read(name: String) = data[name]
        override fun write(name: String, value: String) { data[name] = value }
        override fun delete(name: String) { data.remove(name) }
        override fun deleteUser(userId: String) = Unit
    }
}
