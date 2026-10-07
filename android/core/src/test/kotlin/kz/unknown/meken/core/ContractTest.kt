package kz.unknown.meken.core

import java.time.Clock
import java.time.Instant
import java.time.ZoneOffset
import kotlinx.serialization.SerializationException
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlin.test.Test
import kotlin.test.assertEquals
import kotlin.test.assertFailsWith
import kotlin.test.assertFalse
import kotlin.test.assertIs
import kotlin.test.assertNotNull
import kotlin.test.assertNull
import kotlin.test.assertTrue

class ContractTest {
    private fun listing(): Apartment = API_JSON.decodeFromString(
        assertNotNull(javaClass.getResource("/contract/listing.json")).readText(),
    )

    @Test fun `listing response preserves wire keys money and project evidence`() {
        val value = listing()
        assertEquals(3_550_000_000L, value.priceKzt)
        assertEquals("2026-10-04T10:30:00.123456+05:00", value.observedAt)
        assertEquals("straight_line", value.amenities.single().distanceType)
        assertEquals("Запланировано", value.projectFacts.single().statusLabel)
        assertEquals("В ЖК", value.projectFacts.single().scopeLabel)
        assertFalse(value.isDemo)
        assertTrue(value.canOpenSource)
        assertNull(value.safeImageUrl)
        assertTrue(value.observedAtLabel.contains("10:30"))
        val wire = API_JSON.encodeToString(value)
        assertTrue(wire.contains("\"price_kzt\":3550000000"))
        assertTrue(wire.contains("\"project_facts\""))
        assertFalse(wire.contains("safeSourceUrl"))
        assertEquals(value, API_JSON.decodeFromString<Apartment>(wire))
    }

    @Test fun `optional preference defaults and large budgets roundtrip in request format`() {
        assertEquals(Preferences(), API_JSON.decodeFromString<Preferences>("{}"))
        val prefs = Preferences(city = "Астана", budgetMax = 10_000_000_000L, requiredAmenities = listOf("school"))
        val wire = API_JSON.encodeToString(prefs)
        assertTrue(wire.contains("\"budget_max\":10000000000"))
        assertTrue(wire.contains("\"required_amenities\":[\"school\"]"))
        assertTrue(wire.contains("\"amenity_scope\":\"nearby\""))
        assertFalse(wire.contains(":null"))
        assertEquals(prefs, API_JSON.decodeFromString<Preferences>(wire))
    }

    @Test fun `history snake case payload shares stream decoder and pagination`() {
        val raw = """{
          "id":"conversation-1","title":"Подбор","preferences":{},
          "has_more":true,"next_before":"turn-2","turns":[{
            "id":"turn-3","message":"До 3 миллиардов","status":"complete","events":[{
              "sequence":2,"kind":"preferences",
              "payload":{"city":"Астана","budget_max":3000000000,"required_amenities":["school"]}
            }]
          }]
        }"""
        val history = API_JSON.decodeFromString<ConversationHistory>(raw)
        assertTrue(history.hasMore)
        assertEquals("turn-2", history.nextBefore)
        val updated = SearchState().apply(history.turns.single().events.single().streamEvent())
        assertEquals(3_000_000_000L, updated.preferences.budgetMax)
        assertEquals(listOf("school"), updated.preferences.requiredAmenities)
    }

    @Test fun `authentication legal config verification and conversation wrappers decode`() {
        val token = API_JSON.decodeFromString<TokenPair>("""{"access_token":"a","refresh_token":"r","expires_in":900,"user_id":"u"}""")
        assertEquals(900, token.expiresIn)
        val config = API_JSON.decodeFromString<AppConfig>("""{"mode":"live","ai_enabled":true,"cities":["Астана"],"privacy_url":null,"terms_url":null,"retention_days":90}""")
        assertFalse(config.isDemo)
        assertNull(config.privacyUrl)
        val verification = API_JSON.decodeFromString<Verification>("""{"listing":null,"verification":"unconfirmed","checked_at":"2026-10-04T05:30:00Z"}""")
        assertNull(verification.listing)
        val conversations = API_JSON.decodeFromString<Items<ConversationSummary>>("""{"items":[{"id":"c","title":"Подбор","preferences":{}}]}""")
        assertEquals("c", conversations.items.single().id)
    }

    @Test fun `known malformed event fails while future kinds remain compatible`() {
        assertFailsWith<SerializationException> { decodeEvent("message", """{"citations":[]}""") }
        assertEquals(ServerEvent.Ignored, decodeEvent("future_event", "unrecognized payload"))
        assertEquals(ServerEvent.Accepted("abc"), decodeEvent("accepted", """{"turn_id":"abc"}"""))
        assertEquals(ServerEvent.Failure("Источник не отвечает"), decodeEvent("error", """{"text":"Источник не отвечает"}"""))
        val event = decodeEvent("listings", "{\"items\":[${API_JSON.encodeToString(listing())}]}")
        assertEquals(3_550_000_000L, assertIs<ServerEvent.Listings>(event).items.single().priceKzt)
    }

    @Test fun `raw bytes preserve BOM CRLF CR multiline and Russian UTF8 across any chunks`() {
        val raw = "\uFEFF: keepalive\r\nid: 7\r\nevent: message\r\nretry: 500\r\ndata: {\"text\":\"Квартира 🏠\",\r\ndata: \"citations\":[]}\r\n\r\nevent: done\rdata: {\"status\":\"complete\"}\r\r"
        val bytes = raw.toByteArray(Charsets.UTF_8)
        // Each input byte, including each Cyrillic/emoji byte, can arrive in its own network read.
        val parser = SseByteParser()
        val frames = bytes.asSequence().mapNotNull { parser.feed(it.toInt() and 0xff) }.toList()
        assertEquals(2, frames.size)
        assertEquals(7, frames[0].sequence)
        assertEquals(ServerEvent.Message("Квартира 🏠", emptyList()), decodeEvent(frames[0].kind, frames[0].data))
        assertEquals(ServerEvent.Done("complete"), decodeEvent(frames[1].kind, frames[1].data))
        assertNull(frames[1].sequence)
    }

    @Test fun `comments empty frames and data defaults follow SSE field semantics`() {
        val parser = SseByteParser()
        val frames = ": ping\n\nevent:\ndata:\n\nid: invalid\ndata: hello\n\n".toByteArray()
            .asSequence().mapNotNull { parser.feed(it.toInt() and 0xff) }.toList()
        assertEquals(listOf(SseFrame(null, "message", ""), SseFrame(null, "message", "hello")), frames)
    }

    @Test fun `invalid UTF8 and out of range input do not silently corrupt protocol`() {
        val parser = SseByteParser()
        "data: ".toByteArray().forEach { parser.feed(it.toInt()) }
        parser.feed(0xc3)
        assertFailsWith<SseProtocolException> { parser.feed(10) }
        assertFailsWith<SseProtocolException> { SseByteParser().feed(-1) }
        assertFailsWith<SseProtocolException> { SseByteParser().feed(256) }
    }

    @Test fun `line and aggregate frame bounds include ignored fields`() {
        val hugeLine = SseByteParser()
        repeat(2_000_000) { hugeLine.feed('a'.code) }
        assertFailsWith<SseProtocolException> { hugeLine.feed('a'.code) }
        val aggregate = SseByteParser(maxBytes = 32)
        ":123456789012345\n".toByteArray().forEach { aggregate.feed(it.toInt()) }
        assertFailsWith<SseProtocolException> {
            ":123456789012345\n".toByteArray().forEach { aggregate.feed(it.toInt()) }
        }
    }

    @Test fun `listings replace old results with stable available deduplication`() {
        val first = listing()
        val newer = first.copy(id = "new", version = 13)
        val initial = SearchState(apartments = listOf(first.copy(id = "obsolete")))
        val state = initial
            .apply(ServerEvent.Listings(listOf(first, first.copy(version = 99), newer.copy(status = "sold"), newer)))
        assertEquals(listOf(first, newer), state.apartments)
        assertEquals(listOf(first.copy(id = "obsolete")), initial.apartments)
        assertTrue(state.apply(ServerEvent.Listings(emptyList())).apartments.isEmpty())
    }

    @Test fun `failed turn keeps partial results and original error then clears pending status`() {
        val user = ChatMessage(isUser = true, text = "Квартира в Астане")
        val initial = SearchState(messages = listOf(user), apartments = listOf(listing()), isStreaming = true, status = "Ищем")
        val state = initial.apply(ServerEvent.Accepted("turn-1"))
            .apply(ServerEvent.Pending)
            .apply(ServerEvent.Message("Часть вариантов", emptyList()))
            .apply(ServerEvent.Failure("Источник не отвечает"))
            .apply(ServerEvent.Done("failed"))
        assertEquals("turn-1", state.turnId)
        assertEquals("Источник не отвечает", state.error)
        assertFalse(state.isStreaming)
        assertFalse(state.isPending)
        assertEquals("", state.status)
        assertEquals(2, state.messages.size)
        assertEquals(user, state.messages.first())
        assertEquals(initial.apartments, state.apartments)
        assertTrue(initial.isStreaming)
        assertEquals(1, initial.messages.size)
    }

    @Test fun `freshness needs backend classification and a nonfuture five minute timestamp`() {
        val now = Instant.parse("2026-10-04T05:30:00Z")
        val clock = Clock.fixed(now, ZoneOffset.UTC)
        val apartment = listing().copy(freshness = "recent", observedAt = "2026-10-04T10:25:00+05:00")
        assertTrue(apartment.isRecentAt(clock))
        assertFalse(apartment.copy(observedAt = "2026-10-04T05:24:59.999Z").isRecentAt(clock))
        assertFalse(apartment.copy(observedAt = "2026-10-04T05:30:00.001Z").isRecentAt(clock))
        assertFalse(apartment.copy(freshness = "stale").isRecentAt(clock))
        assertFalse(apartment.copy(observedAt = "invalid").isRecentAt(clock))
    }

    @Test fun `external links reject credentials insecure schemes hostless and malformed ports`() {
        listOf("http://bi.group/ru", "javascript:alert(1)", "https:///path", "https://user:password@bi.group/ru",
            "https://user@bi.group", "https://bi.group:0/", "https://bi.group:65536/", "https://bi.group/\nnext")
            .forEach { assertNull(safeHttpsUrl(it), it) }
        assertNull(safeHttpsUrl(null))
        assertEquals("https://bi.group/ru", safeHttpsUrl("https://bi.group/ru"))
        assertNotNull(safeHttpsUrl("https://example.com:8443/path?q=a%20b"))
        assertNull(Citation("id", "Demo", "https://bi.group", demo = true).safeUrl)
    }
}
