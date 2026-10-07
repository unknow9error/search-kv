package kz.unknown.meken.data

import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.TokenPair
import kz.unknown.meken.core.Preferences
import kotlinx.coroutines.runBlocking
import org.junit.Assert.*
import org.junit.Test

class MekenRepositoryTest {
    @Test fun `late creation cleanup cannot erase a different durable conversation request`() = runBlocking {
        val repository = repository()
        val first = PendingConversation(preferences = Preferences(city = "Астана"), firstTurn = PendingTurn(conversationId = "client-a", message = "Первый запрос"))
        val next = PendingConversation(preferences = Preferences(city = "Алматы"), firstTurn = PendingTurn(conversationId = "client-b", message = "Новый запрос"))
        repository.savePendingConversation(first)
        repository.savePendingConversation(next)
        repository.clearPendingConversation(first.clientConversationId)
        assertEquals(next, repository.pendingConversation())
        repository.clearPendingConversation(next.clientConversationId)
        assertNull(repository.pendingConversation())
    }

    @Test fun `late clear for old request cannot erase a newly persisted turn`() = runBlocking {
        val repository = repository()
        val original = PendingTurn(conversationId = "conversation-a", message = "Квартира в Астане")
        repository.savePending(original)
        val replacement = PendingTurn(conversationId = "conversation-b", message = "Две комнаты")
        repository.savePending(replacement)

        // Old cancellation/cache cleanup arrives after the new conversation has saved its request.
        repository.clearPending(original.clientTurnId)
        assertEquals(replacement, repository.pending())
        repository.clearPending(replacement.clientTurnId)
        assertNull(repository.pending())
    }

    @Test fun `repository revisions stay monotonic across screen owners and reject stale cache writes`() = runBlocking {
        val repository = repository()
        val firstOwnerRevision = repository.nextFavoritesRevision()
        val old = listing("old")
        repository.saveFavorites(listOf(old), firstOwnerRevision)
        // Another ViewModel can be created while this process and singleton repository remain alive.
        val secondOwnerRevision = repository.nextFavoritesRevision()
        assertTrue(secondOwnerRevision > firstOwnerRevision)
        val fresh = listing("fresh")
        repository.saveFavorites(listOf(fresh), secondOwnerRevision)
        repository.saveFavorites(listOf(old), firstOwnerRevision)
        assertEquals(listOf(fresh), repository.cachedFavorites())
    }

    private suspend fun repository(): MekenRepository {
        val session = object : SessionStorage {
            override fun load() = TokenPair("a".repeat(43), "r".repeat(64), 3600, "test-user")
            override fun save(tokens: TokenPair) = Unit
            override fun clear() = Unit
            override fun isExpired() = false
            override fun markExpired() = Unit
            override fun clearExpired() = Unit
        }
        val api = ApiClient("https://api.example.test", session)
        api.bootstrap() // Uses the injected existing session; no network is called.
        val records = object : PrivateRecords {
            private val values = mutableMapOf<String, String>()
            @Synchronized override fun read(name: String) = values[name]
            @Synchronized override fun write(name: String, value: String) { values[name] = value }
            @Synchronized override fun delete(name: String) { values.remove(name) }
            @Synchronized override fun deleteUser(userId: String) { values.keys.removeAll { it.endsWith(storageHash(userId)) } }
        }
        return MekenRepository(api, records)
    }

    private fun listing(id: String) = Apartment(
        id = id, providerId = "test", providerName = "Test", complexName = "Test", city = "Астана",
        district = "Test", address = "Test", rooms = 2, areaM2 = 60.0, floor = 3, totalFloors = 10,
        priceKzt = 35_000_000, status = "available", finish = "Test", completion = "Test",
        sourceUrl = "https://example.test/listing", observedAt = "2026-10-04T00:00:00Z", version = 1,
        provenance = "demo", freshness = "stale", reasons = emptyList(), tradeoffs = emptyList(), amenities = emptyList(),
    )
}
