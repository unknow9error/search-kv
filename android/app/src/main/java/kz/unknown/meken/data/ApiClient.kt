package kz.unknown.meken.data

import java.io.IOException
import java.util.concurrent.TimeUnit
import kotlin.coroutines.resumeWithException
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.ServerEvent
import kz.unknown.meken.core.SseByteParser
import kz.unknown.meken.core.TokenPair
import kz.unknown.meken.core.decodeEvent
import kotlinx.coroutines.CoroutineStart
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.coroutineScope
import kotlinx.coroutines.currentCoroutineContext
import kotlinx.coroutines.ensureActive
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.channelFlow
import kotlinx.coroutines.launch
import kotlinx.coroutines.suspendCancellableCoroutine
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.jsonPrimitive
import okhttp3.Call
import okhttp3.Callback
import okhttp3.HttpUrl
import okhttp3.HttpUrl.Companion.toHttpUrl
import okhttp3.MediaType.Companion.toMediaType
import okhttp3.OkHttpClient
import okhttp3.Request
import okhttp3.RequestBody.Companion.toRequestBody
import okhttp3.Response

class SessionExpired : Exception("Сессия завершилась. Начните новый подбор.")
class InvalidResponse : Exception("Не удалось прочитать ответ сервиса. Повторите попытку.")
class StreamInterrupted : Exception("Связь прервалась. Проверьте сохранённый ответ.")
class ApiFailure(val status: Int, val code: String) : Exception() {
    val userMessage: String get() = when (code) {
        "rate_limited" -> "Слишком много запросов. Попробуйте немного позже."
        "turn_in_progress" -> "Текущий подбор ещё идёт. Дождитесь его завершения."
        "project_not_found" -> "Этот ЖК больше не доступен в каталоге."
        "layout_not_found" -> "Эта планировка больше не доступна в каталоге."
        "apartment_not_found" -> "Это предложение больше не доступно в каталоге."
        "conversation_not_found" -> "Этот диалог больше не доступен. Начните новый."
        "idempotency_conflict" -> "Сохранённый запрос изменился. Начните новый диалог."
        "invalid_recovery_code" -> "Неверный код восстановления. Проверьте код и попробуйте снова."
        "recovery_unavailable" -> "Восстановление аккаунта пока недоступно. Попробуйте позже."
        "favorites_limit" -> "Сохранено уже 200 объектов. Удалите ненужные, чтобы добавить новые."
        "conversation_limit", "conversations_limit" -> "Достигнут лимит диалогов. Удалите ненужные диалоги."
        else -> "Сервис пока недоступен. Проверьте интернет и повторите попытку."
    }
}

data class StreamUpdate(val sequence: Int?, val event: ServerEvent)

/** Auth is serialized, including durable token rotation. Ordinary requests run concurrently. */
class ApiClient(baseUrl: String, private val storage: SessionStorage, private val client: OkHttpClient = defaultClient(), allowInsecureLocalDebug: Boolean = false) {
    val endpoint: String
    private val base: HttpUrl
    private val authMutex = Mutex()
    private val recoveryCodeMutex = Mutex()
    @Volatile private var tokens: TokenPair? = null
    @Volatile private var expired = false

    init {
        val parsed = baseUrl.toHttpUrl()
        val localDebug = allowInsecureLocalDebug && parsed.scheme == "http" && parsed.host in setOf("10.0.2.2", "127.0.0.1", "localhost")
        require((parsed.isHttps || localDebug) && parsed.username.isEmpty() && parsed.password.isEmpty() && parsed.query == null && parsed.fragment == null) {
            "Адрес сервиса должен использовать HTTPS."
        }
        base = parsed.newBuilder().apply { if (!parsed.encodedPath.endsWith('/')) addPathSegment("") }.build()
        endpoint = base.toString()
    }

    fun currentUserId(): String? = tokens?.userId

    suspend fun bootstrap() = authMutex.withLock {
        if (expired || withContext(Dispatchers.IO) { storage.isExpired() }) { expired = true; throw SessionExpired() }
        if (tokens != null) return@withLock
        val saved = withContext(Dispatchers.IO) { storage.load() }
        if (saved != null) { tokens = saved; return@withLock }
        val created = decodeTokens(rawUnauthenticated("v1/auth/anonymous", "POST", null))
        withContext(NonCancellable + Dispatchers.IO) { storage.save(created); tokens = created }
    }

    private suspend fun refresh(accessUsed: String) = authMutex.withLock {
        if (expired) throw SessionExpired()
        val old = tokens ?: throw SessionExpired()
        if (old.accessToken != accessUsed) return@withLock
        // A rotating, single-use refresh belongs to the session. Finish it even if one caller leaves.
        withContext(NonCancellable) { try {
            val value = rawUnauthenticated("v1/auth/refresh", "POST", API_JSON.encodeToString(RefreshBody(old.refreshToken)))
            val refreshed = decodeTokens(value)
            if (refreshed.userId != old.userId) throw InvalidResponse()
            // Server refresh is single-use: cancellation must not discard a successful replacement.
            withContext(NonCancellable + Dispatchers.IO) { storage.save(refreshed); tokens = refreshed }
        } catch (error: ApiFailure) {
            if (error.status == 401) {
                withContext(Dispatchers.IO) { storage.markExpired(); expired = true }
                throw SessionExpired()
            }
            throw error
        } }
    }

    private fun request(path: String, method: String, body: String?, access: String? = null, streaming: Boolean = false): Request {
        require(!path.startsWith('/') && !path.contains("..") && !path.contains('#'))
        val url = base.resolve(path) ?: throw InvalidResponse()
        require(url.scheme == base.scheme && url.host == base.host && url.port == base.port)
        val content = body?.toRequestBody("application/json; charset=utf-8".toMediaType())
            ?: if (method in listOf("POST", "PUT", "PATCH")) "".toRequestBody("application/json".toMediaType()) else null
        return Request.Builder().url(url).method(method, content)
            .header("Accept", if (streaming) "text/event-stream" else "application/json")
            .header("Cache-Control", "no-store")
            .apply { if (access != null) header("Authorization", "Bearer $access") }.build()
    }

    private suspend fun open(path: String, method: String, body: String?, authenticated: Boolean, streaming: Boolean = false): Pair<Call, Response> {
        if (authenticated) bootstrap()
        val accessUsed = if (authenticated) tokens?.accessToken ?: throw SessionExpired() else null
        var call = client.newCall(request(path, method, body, accessUsed, streaming))
        var response = try { call.awaitResponse() } catch (failure: IOException) {
            currentCoroutineContext().ensureActive()
            val safeReplay = method == "GET" || (method == "POST" && path.substringBefore('?') in setOf("v1/projects/search", "v1/projects/map", "v1/projects/compare")) || (method in setOf("PUT", "DELETE") && path.startsWith("v1/projects/favorites/"))
            val closedBeforeHeaders = failure.cause is java.io.EOFException || failure.message?.startsWith("unexpected end of stream") == true
            if (!safeReplay || !closedBeforeHeaders) throw failure
            // A pooled HTTP/1 socket may have expired while the user was reading a page.
            // Catalogue reads and declarative favorite updates are safe to replay; account and turn writes are not.
            // Other idle sockets can have expired at the same time; retry on a fresh connection.
            client.connectionPool.evictAll()
            call = client.newCall(request(path, method, body, accessUsed, streaming))
            call.awaitResponse()
        }
        if (authenticated && response.code == 401) {
            response.close()
            refresh(accessUsed!!)
            val replayAccess = tokens?.accessToken ?: throw SessionExpired()
            call = client.newCall(request(path, method, body, replayAccess, streaming))
            response = call.awaitResponse()
            if (response.code == 401) {
                response.close()
                authMutex.withLock {
                    if (tokens?.accessToken == replayAccess) withContext(NonCancellable + Dispatchers.IO) { storage.markExpired(); expired = true }
                }
                throw SessionExpired()
            }
        }
        return call to response
    }

    @PublishedApi internal suspend fun raw(path: String, method: String = "GET", body: String? = null, authenticated: Boolean = true): String {
        val (call, response) = open(path, method, body, authenticated)
        return withResponse(call, response) {
            val value = response.readBoundedJson()
            if (!response.isSuccessful) throw failure(response.code, value)
            value.ifEmpty { "{}" }
        }
    }

    private suspend fun rawUnauthenticated(path: String, method: String, body: String?) = raw(path, method, body, authenticated = false)
    suspend inline fun <reified T> call(path: String, method: String = "GET", body: String? = null, authenticated: Boolean = true): T {
        val value = raw(path, method, body, authenticated)
        return try { API_JSON.decodeFromString<T>(value) } catch (_: Exception) { throw InvalidResponse() }
    }
    suspend fun perform(path: String, method: String) { raw(path, method) }

    fun stream(path: String, method: String, body: String? = null): Flow<StreamUpdate> = channelFlow {
        val (call, response) = open(path, method, body, authenticated = true, streaming = true)
        withResponse(call, response) {
            if (!response.isSuccessful) throw failure(response.code, response.readBoundedJson())
            if (response.header("Content-Type")?.substringBefore(';')?.trim() != "text/event-stream") throw InvalidResponse()
            response.header("X-Turn-ID")?.let { id ->
                if (runCatching { java.util.UUID.fromString(id) }.isFailure) throw InvalidResponse()
                send(StreamUpdate(null, ServerEvent.Accepted(id)))
            }
            val bytes = response.body.byteStream()
            val parser = SseByteParser()
            val buffer = ByteArray(8192)
            while (true) {
                currentCoroutineContext().ensureActive()
                val count = bytes.read(buffer)
                if (count == -1) break
                for (i in 0 until count) {
                    parser.feed(buffer[i].toInt() and 0xff)?.let { frame ->
                        send(StreamUpdate(frame.sequence, decodeEvent(frame.kind, frame.data)))
                    }
                }
            }
        }
    }

    suspend fun deleteAccount() {
        perform("v1/me", "DELETE")
        authMutex.withLock { withContext(NonCancellable + Dispatchers.IO) { storage.clear(); tokens = null } }
    }

    suspend fun generateRecoveryCode(): String = recoveryCodeMutex.withLock {
        call<RecoveryResponse>("v1/auth/recovery-code", "POST").recoveryCode.also {
            if (!it.matches(Regex("[A-Za-z0-9_-]{43}"))) throw InvalidResponse()
        }
    }

    /** Existing credentials remain untouched until a complete successful recovery response is durable. */
    suspend fun recoverAccount(code: String): String? = authMutex.withLock {
        val oldUser = tokens?.userId ?: withContext(Dispatchers.IO) { runCatching { storage.load()?.userId }.getOrNull() }
        val recovered = try {
            decodeTokens(rawUnauthenticated("v1/auth/recover", "POST", API_JSON.encodeToString(RecoveryBody(code))))
        } catch (error: ApiFailure) {
            if (error.status == 404) throw ApiFailure(404, "recovery_unavailable")
            throw error
        }
        withContext(NonCancellable + Dispatchers.IO) {
            storage.save(recovered)
            storage.clearExpired()
            tokens = recovered
            expired = false
        }
        oldUser
    }

    /** Only called after the user agrees to create a new anonymous account. */
    suspend fun resetSession(): String? = authMutex.withLock {
        val oldUser = tokens?.userId ?: withContext(Dispatchers.IO) { storage.load()?.userId }
        withContext(NonCancellable + Dispatchers.IO) { storage.clear(); tokens = null; expired = false }
        oldUser
    }

    private fun decodeTokens(value: String): TokenPair = try {
        API_JSON.decodeFromString<TokenPair>(value).also {
            if (it.accessToken.length !in 32..128 || it.refreshToken.length !in 32..128 || it.userId.isBlank()) throw InvalidResponse()
        }
    } catch (_: Exception) { throw InvalidResponse() }

    @Serializable private data class RefreshBody(@SerialName("refresh_token") val refreshToken: String)
    @Serializable private data class RecoveryBody(@SerialName("recovery_code") val recoveryCode: String)
    @Serializable private data class RecoveryResponse(@SerialName("recovery_code") val recoveryCode: String)

    companion object {
        fun defaultClient(): OkHttpClient = OkHttpClient.Builder()
            .connectTimeout(20, TimeUnit.SECONDS).readTimeout(150, TimeUnit.SECONDS)
            .callTimeout(180, TimeUnit.SECONDS).cache(null)
            .followRedirects(false).followSslRedirects(false).retryOnConnectionFailure(false).build()

        private fun failure(status: Int, value: String): ApiFailure {
            val code = runCatching {
                API_JSON.parseToJsonElement(value).jsonObject["error"]?.jsonObject?.get("code")?.jsonPrimitive?.content
            }.getOrNull() ?: "temporarily_unavailable"
            return ApiFailure(status, code)
        }
    }
}

private suspend fun Call.awaitResponse(): Response = suspendCancellableCoroutine { continuation ->
    continuation.invokeOnCancellation { cancel() }
    enqueue(object : Callback {
        override fun onFailure(call: Call, e: IOException) { if (continuation.isActive) continuation.resumeWithException(e) }
        override fun onResponse(call: Call, response: Response) {
            continuation.resume(response) { _, rejected, _ -> rejected.close() }
        }
    })
}

private fun Response.readBoundedJson(): String {
    val content = body
    val limit = 10L * 1024 * 1024
    if (content.contentLength() > limit) throw InvalidResponse()
    val source = content.source()
    source.request(limit + 1)
    if (source.buffer.size > limit) throw InvalidResponse()
    return source.readUtf8()
}

/** Socket cancellation unblocks the reader; its buffered source has one owner until it exits. */
private suspend fun <T> withResponse(call: Call, response: Response, block: suspend () -> T): T = coroutineScope {
    val completed = java.util.concurrent.atomic.AtomicBoolean(false)
    val watcher = launch(start = CoroutineStart.UNDISPATCHED) {
        // ResponseBody.close() can drain an HTTP/1 body. Calling it concurrently with read()
        // races Okio's buffered source and AsyncTimeout. Cancel the socket here instead.
        try { awaitCancellation() } finally { if (!completed.get()) call.cancel() }
    }
    try {
        withContext(Dispatchers.IO) {
            try { block().also { completed.set(true) } }
            catch (error: IOException) {
                // Closing the cancelled socket may surface as a transport exception (including
                // HTTP/2 CANCEL). Preserve cancellation so this cannot fail the parent scope.
                currentCoroutineContext().ensureActive()
                throw error
            }
        }
    }
    finally { withContext(NonCancellable + Dispatchers.IO) { watcher.cancelAndJoin(); response.close() } }
}
