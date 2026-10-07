package kz.unknown.meken.data

import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import java.util.concurrent.atomic.AtomicBoolean
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.ServerEvent
import kz.unknown.meken.core.TokenPair
import kz.unknown.meken.core.Preferences
import kz.unknown.meken.core.ConversationSummary
import kotlinx.coroutines.CompletableDeferred
import kotlinx.coroutines.async
import kotlinx.coroutines.awaitAll
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.launch
import kotlinx.coroutines.runBlocking
import kotlinx.coroutines.withTimeout
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonObject
import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.ConcurrentHashMap
import okhttp3.OkHttpClient
import okhttp3.Protocol
import okhttp3.Call
import okhttp3.EventListener
import okhttp3.mockwebserver.Dispatcher
import okhttp3.mockwebserver.MockResponse
import okhttp3.mockwebserver.MockWebServer
import okhttp3.mockwebserver.RecordedRequest
import okhttp3.mockwebserver.SocketPolicy
import okhttp3.tls.HandshakeCertificates
import okhttp3.tls.HeldCertificate
import org.junit.Assert.*
import org.junit.Test

/** Real OkHttp over isolated HTTPS verifies auth races without changing production data. */
class ApiClientTest {
    private val old = TokenPair("a".repeat(43), "r".repeat(64), 3600, "user-1")
    private val fresh = TokenPair("b".repeat(43), "s".repeat(64), 3600, "user-1")

    @Test fun `read only project request retries a socket closed before headers`() = runBlocking {
        fixture(old).use { test ->
            test.server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
            test.server.enqueue(MockResponse().setBody("{\"items\":[]}"))
            val api = ApiClient(test.url, test.storage, test.client.newBuilder().protocols(listOf(Protocol.HTTP_1_1)).retryOnConnectionFailure(false).build())
            val reply = api.call<kotlinx.serialization.json.JsonObject>("v1/projects/map", "POST", "{\"criteria\":{}}")
            assertNotNull(reply["items"])
        }
    }
    @Test fun `account writes are not retried after a socket failure`() = runBlocking {
        fixture(old).use { test ->
            test.server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.DISCONNECT_AFTER_REQUEST))
            test.server.enqueue(MockResponse().setBody("{}"))
            val api = ApiClient(test.url, test.storage, test.client.newBuilder().protocols(listOf(Protocol.HTTP_1_1)).retryOnConnectionFailure(false).build())
            assertTrue(runCatching { api.call<kotlinx.serialization.json.JsonObject>("v1/auth/anonymous", "POST", authenticated = false) }.isFailure)
        }
    }

    @Test fun `recovery installs a new independent session for the same user and clears expiry`() = runBlocking {
        fixture(old).use { test ->
            test.storage.expired = true
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    "/v1/auth/recover" -> { assertNull(request.getHeader("Authorization")); tokens(fresh) }
                    "/v1/me" -> { assertEquals("Bearer ${fresh.accessToken}", request.getHeader("Authorization")); MockResponse().setBody("{}") }
                    else -> error(404)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            assertTrue(runCatching { api.bootstrap() }.exceptionOrNull() is SessionExpired)
            assertEquals(old.userId, api.recoverAccount("c".repeat(43)))
            assertEquals(fresh, test.storage.saved)
            assertFalse(test.storage.expired)
            assertEquals(old.userId, api.currentUserId())
            api.perform("v1/me", "GET")
        }
    }

    @Test fun `invalid recovery retains old credentials and all local private records`() = runBlocking {
        fixture(old).use { test ->
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    "/v1/auth/recover" -> MockResponse().setResponseCode(401).setBody("{\"error\":{\"code\":\"invalid_recovery_code\"}}")
                    "/v1/me" -> { assertEquals("Bearer ${old.accessToken}", request.getHeader("Authorization")); MockResponse().setBody("{}") }
                    else -> error(404)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            val records = MemoryRecords()
            val repository = MekenRepository(api, records)
            repository.bootstrap()
            val turn = PendingTurn(conversationId = "saved", message = "Сохранённый запрос")
            repository.savePending(turn)
            repository.savePendingConversation(PendingConversation(preferences = Preferences(city = "Астана"), firstTurn = turn))
            records.write("favorites.${storageHash(old.userId)}", "previous-private-cache")
            val before = records.values.toMap()
            val failure = runCatching { repository.recoverAccount("c".repeat(43)) }.exceptionOrNull()
            assertTrue(failure is ApiFailure && failure.code == "invalid_recovery_code")
            assertEquals(old, test.storage.saved)
            assertFalse(test.storage.expired)
            assertEquals(before, records.values.toMap())
            assertEquals(turn, repository.pending())
            api.perform("v1/me", "GET")
        }
    }

    @Test fun `conversation client key is included only when the service advertises support`() = runBlocking {
        fixture(old).use { test ->
            val supported = AtomicBoolean()
            val preferences = Preferences(city = "Астана", rooms = listOf(2))
            val bodies = CopyOnWriteArrayList<String>()
            val created = ConversationSummary("3d7f9549-73e7-4c2c-97d7-3a30f499753b", "Поиск", preferences)
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when (request.path) {
                    "/v1/config" -> MockResponse().setBody("{\"mode\":\"live\",\"ai_enabled\":true,\"cities\":[],\"retention_days\":30" +
                        if (supported.get()) ",\"capabilities\":[\"idempotent_conversation_create\"]}" else "}")
                    "/v1/conversations" -> { bodies += request.body.readUtf8(); MockResponse().setResponseCode(201).setBody(API_JSON.encodeToString(created)) }
                    else -> error(404)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            val records = object : PrivateRecords {
                override fun read(name: String): String? = null
                override fun write(name: String, value: String) = Unit
                override fun delete(name: String) = Unit
                override fun deleteUser(userId: String) = Unit
            }
            val repository = MekenRepository(api, records)
            val request = PendingConversation(preferences = preferences, firstTurn = PendingTurn(conversationId = "pending", message = "Запрос"))
            repository.bootstrap()
            repository.config()
            repository.createConversation(request)
            supported.set(true)
            repository.config()
            repository.createConversation(request)
            val legacy = API_JSON.parseToJsonElement(bodies[0]).jsonObject
            val capable = API_JSON.parseToJsonElement(bodies[1]).jsonObject
            assertFalse(legacy.containsKey("client_conversation_id"))
            assertEquals(request.clientConversationId, capable.getValue("client_conversation_id").toString().trim('"'))
            assertEquals(legacy.getValue("preferences"), capable.getValue("preferences"))
        }
    }

    @Test fun `concurrent first requests enroll only once`() = runBlocking {
        fixture().use { test ->
            val enrollments = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    assertEquals("/v1/auth/anonymous", request.path)
                    enrollments.incrementAndGet()
                    return tokens(old)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            (1..12).map { async { api.bootstrap() } }.awaitAll()
            assertEquals(1, enrollments.get())
            assertEquals(old, test.storage.saved)
        }
    }

    @Test fun `parallel 401 responses share one refresh and replay each request once`() = runBlocking {
        fixture(old).use { test ->
            val refreshes = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when {
                    request.path == "/v1/auth/refresh" -> { refreshes.incrementAndGet(); tokens(fresh).setBodyDelay(80, TimeUnit.MILLISECONDS) }
                    request.getHeader("Authorization") == "Bearer ${old.accessToken}" -> error(401)
                    request.getHeader("Authorization") == "Bearer ${fresh.accessToken}" -> MockResponse().setBody("{}")
                    else -> error(500)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            (1..8).map { async { api.perform("v1/me", "GET") } }.awaitAll()
            assertEquals(1, refreshes.get())
            assertEquals(fresh, test.storage.saved)
            assertFalse(test.storage.expired)
        }
    }

    @Test fun `transient refresh failure retains the old pair for a later retry`() = runBlocking {
        fixture(old).use { test ->
            val refreshes = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when {
                    request.path == "/v1/auth/refresh" -> if (refreshes.incrementAndGet() == 1) error(503) else tokens(fresh)
                    request.getHeader("Authorization") == "Bearer ${old.accessToken}" -> error(401)
                    else -> MockResponse().setBody("{}")
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            val first = runCatching { api.perform("v1/me", "GET") }.exceptionOrNull()
            assertTrue(first is ApiFailure && first.status == 503)
            assertEquals(old, test.storage.saved)
            assertFalse(test.storage.expired)
            api.perform("v1/me", "GET")
            assertEquals(fresh, test.storage.saved)
        }
    }

    @Test fun `cancelling refresh initiator still durably retains rotated single-use tokens`() = runBlocking {
        fixture(old).use { test ->
            val refreshes = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = when {
                    request.path == "/v1/auth/refresh" -> { refreshes.incrementAndGet(); tokens(fresh).setBodyDelay(200, TimeUnit.MILLISECONDS) }
                    request.getHeader("Authorization") == "Bearer ${old.accessToken}" -> error(401)
                    else -> MockResponse().setBody("{}")
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            val initiator = launch { api.perform("v1/me", "GET") }
            withTimeout(5000) {
                kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) {
                    assertEquals("/v1/me", test.server.takeRequest(4, TimeUnit.SECONDS)?.path)
                    assertEquals("/v1/auth/refresh", test.server.takeRequest(4, TimeUnit.SECONDS)?.path)
                }
            }
            withTimeout(3000) { initiator.cancelAndJoin() }
            assertEquals(fresh, test.storage.saved)
            api.perform("v1/me", "GET")
            assertEquals(1, refreshes.get())
        }
    }

    @Test fun `expired session stays expired across clients until explicit reset`() = runBlocking {
        fixture(old).use { test ->
            val calls = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse {
                    calls.incrementAndGet()
                    return if (request.path == "/v1/auth/anonymous") tokens(fresh) else error(401)
                }
            }
            val api = ApiClient(test.url, test.storage, test.client)
            assertTrue(runCatching { api.perform("v1/me", "GET") }.exceptionOrNull() is SessionExpired)
            assertTrue(test.storage.expired)
            val before = calls.get()
            assertTrue(runCatching { api.perform("v1/me", "GET") }.exceptionOrNull() is SessionExpired)
            val recreated = ApiClient(test.url, test.storage, test.client)
            assertTrue(runCatching { recreated.bootstrap() }.exceptionOrNull() is SessionExpired)
            assertEquals(before, calls.get())
            assertEquals(old.userId, recreated.resetSession())
            recreated.bootstrap()
            assertFalse(test.storage.expired)
            assertEquals(before + 1, calls.get())
        }
    }

    @Test fun `second 401 marks expiry without an endless refresh loop`() = runBlocking {
        fixture(old).use { test ->
            val refreshes = AtomicInteger()
            test.server.dispatcher = object : Dispatcher() {
                override fun dispatch(request: RecordedRequest): MockResponse = if (request.path == "/v1/auth/refresh") {
                    refreshes.incrementAndGet(); tokens(fresh)
                } else error(401)
            }
            val api = ApiClient(test.url, test.storage, test.client)
            assertTrue(runCatching { api.perform("v1/me", "GET") }.exceptionOrNull() is SessionExpired)
            assertEquals(1, refreshes.get())
            assertTrue(test.storage.expired)
        }
    }

    @Test fun `turn header survives a lost accepted frame and cancellation closes the stream`() = runBlocking {
        for (protocols in listOf(listOf(Protocol.HTTP_1_1), listOf(Protocol.HTTP_2, Protocol.HTTP_1_1))) repeat(3) {
            fixture(old, protocols).use { test ->
                val turn = "3d7f9549-73e7-4c2c-97d7-3a30f499753b"
                test.server.enqueue(MockResponse().setHeader("Content-Type", "text/event-stream")
                    .setHeader("X-Turn-ID", turn).setBody("event: status\ndata: {\"text\":\"Подбираем\"}\n\n")
                    .throttleBody(1, 1, TimeUnit.SECONDS))
                val bodyStarted = CompletableDeferred<Unit>()
                val streamClient = test.client.newBuilder().eventListener(object : EventListener() {
                    override fun responseBodyStart(call: Call) { bodyStarted.complete(Unit) }
                }).build()
                val api = ApiClient(test.url, test.storage, streamClient)
                val header = CompletableDeferred<ServerEvent>()
                val job = launch {
                    api.stream("v1/conversations/$turn/turns", "POST", "{}").collect { update -> header.complete(update.event) }
                }
                assertEquals(ServerEvent.Accepted(turn), withTimeout(5000) { header.await() })
                withTimeout(5000) { bodyStarted.await() }
                withTimeout(3000) { job.cancelAndJoin() }
                assertEquals(0, test.client.dispatcher.runningCallsCount())
            }
        }
    }

    @Test fun `cancelling before headers cancels the OkHttp call`() = runBlocking {
        fixture(old).use { test ->
            test.server.enqueue(MockResponse().setSocketPolicy(SocketPolicy.NO_RESPONSE))
            val api = ApiClient(test.url, test.storage, test.client)
            val job = launch { api.perform("v1/me", "GET") }
            withTimeout(5000) { kotlinx.coroutines.withContext(kotlinx.coroutines.Dispatchers.IO) { test.server.takeRequest(4, TimeUnit.SECONDS) } }
            withTimeout(3000) { job.cancelAndJoin() }
            // Callback dispatch may finish just after the coroutine cancellation.
            withTimeout(3000) {
                while (test.client.dispatcher.runningCallsCount() != 0) kotlinx.coroutines.yield()
            }
        }
    }

    @Test fun `production rejects cleartext and debug restricts it to loopback`() {
        val storage = MemoryStorage(null)
        assertTrue(runCatching { ApiClient("http://example.com", storage) }.isFailure)
        assertTrue(runCatching { ApiClient("http://example.com", storage, allowInsecureLocalDebug = true) }.isFailure)
        assertTrue(runCatching { ApiClient("http://10.0.2.2:8002", storage, allowInsecureLocalDebug = true) }.isSuccess)
    }

    private fun tokens(pair: TokenPair) = MockResponse().setHeader("Content-Type", "application/json").setBody(API_JSON.encodeToString(pair))
    private fun error(status: Int) = MockResponse().setResponseCode(status).setBody("{\"error\":{\"code\":\"session_expired\"}}")

    private class MemoryStorage(@Volatile var saved: TokenPair?) : SessionStorage {
        @Volatile var expired = false
        override fun load() = saved
        override fun save(tokens: TokenPair) { saved = tokens }
        override fun clear() { saved = null; expired = false }
        override fun isExpired() = expired
        override fun markExpired() { expired = true }
        override fun clearExpired() { expired = false }
    }
    private class MemoryRecords : PrivateRecords {
        val values = ConcurrentHashMap<String, String>()
        override fun read(name: String) = values[name]
        override fun write(name: String, value: String) { values[name] = value }
        override fun delete(name: String) { values.remove(name) }
        override fun deleteUser(userId: String) { values.keys.removeAll { it.endsWith(storageHash(userId)) } }
    }
    private class Fixture(val server: MockWebServer, val client: OkHttpClient, val storage: MemoryStorage) : AutoCloseable {
        val url: String get() = server.url("/").toString()
        override fun close() { client.connectionPool.evictAll(); client.dispatcher.executorService.shutdown(); server.shutdown() }
    }
    private fun fixture(saved: TokenPair? = null, protocols: List<Protocol> = listOf(Protocol.HTTP_2, Protocol.HTTP_1_1)): Fixture {
        val certificate = HeldCertificate.Builder().commonName("localhost").addSubjectAlternativeName("localhost").build()
        val serverCertificates = HandshakeCertificates.Builder().heldCertificate(certificate).build()
        val clientCertificates = HandshakeCertificates.Builder().addTrustedCertificate(certificate.certificate).build()
        val server = MockWebServer().apply { this.protocols = protocols; useHttps(serverCertificates.sslSocketFactory(), false); start() }
        val client = ApiClient.defaultClient().newBuilder()
            .protocols(protocols)
            .sslSocketFactory(clientCertificates.sslSocketFactory(), clientCertificates.trustManager).build()
        return Fixture(server, client, MemoryStorage(saved))
    }
}
