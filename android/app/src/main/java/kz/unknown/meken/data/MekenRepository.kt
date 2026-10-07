package kz.unknown.meken.data

import android.content.Context
import java.util.UUID
import java.util.concurrent.atomic.AtomicLong
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.ConversationHistory
import kz.unknown.meken.core.ConversationSummary
import kz.unknown.meken.core.Items
import kz.unknown.meken.core.Preferences
import kz.unknown.meken.core.Verification
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.NonCancellable
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.withContext
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.decodeFromString
import kotlinx.serialization.encodeToString
import okhttp3.HttpUrl.Companion.toHttpUrl

/** The immutable body is retained before POST, including when no accepted frame reaches the app. */
@Serializable data class PendingTurn(
    val conversationId: String,
    val clientTurnId: String = UUID.randomUUID().toString(),
    val message: String,
    val selectedListingIds: List<String> = emptyList(),
    val turnId: String? = null,
    val afterSequence: Int = 0,
)

/** Includes the first turn so a lost creation response cannot lose the user's original request. */
@Serializable data class PendingConversation(
    val clientConversationId: String = UUID.randomUUID().toString(),
    val preferences: Preferences,
    val firstTurn: PendingTurn,
)

class MekenRepository(internal val api: ApiClient, internal val files: PrivateRecords) : MobileRepository {
    private val pendingMutex = Mutex()
    private val favoritesMutex = Mutex()
    private var favoritesRevision = -1L
    private val favoriteClock = AtomicLong()
    @Volatile private var idempotentConversationCreation = false
    override fun nextFavoritesRevision(): Long = favoriteClock.incrementAndGet()
    override val userId: String? get() = api.currentUserId()
    override suspend fun bootstrap() = api.bootstrap()
    override suspend fun config(): AppConfig = api.call<AppConfig>("v1/config", authenticated = false).also {
        idempotentConversationCreation = "idempotent_conversation_create" in it.capabilities
    }
    override suspend fun conversations(): List<ConversationSummary> = api.call<Items<ConversationSummary>>("v1/conversations").items
    override suspend fun history(id: String, before: String?): ConversationHistory = api.call(
        "v1/conversations/${identifier(id)}" + if (before == null) "" else "?before=${identifier(before)}")
    override suspend fun deleteConversation(id: String) = api.perform("v1/conversations/${identifier(id)}", "DELETE")
    override suspend fun createConversation(request: PendingConversation): ConversationSummary = api.call(
        "v1/conversations", "POST", API_JSON.encodeToString(CreateBody(request.preferences,
            request.clientConversationId.takeIf { idempotentConversationCreation })))
    override suspend fun updatePreferences(id: String, preferences: Preferences): Preferences = api.call(
        "v1/conversations/${identifier(id)}/preferences", "PUT", API_JSON.encodeToString(preferences))
    override suspend fun favorites(): List<Apartment> = api.call<Items<Apartment>>("v1/favorites").items
    override suspend fun favorite(id: String, remove: Boolean) = api.perform("v1/favorites/${identifier(id)}", if (remove) "DELETE" else "PUT")
    override suspend fun verify(id: String): Verification = api.call("v1/apartments/${identifier(id)}/verify", "POST")

    private fun record(kind: String): String = "$kind.${storageHash(userId ?: throw SessionExpired())}"
    override suspend fun cachedFavorites(): List<Apartment> = withContext(Dispatchers.IO) {
        files.read(record("favorites"))?.let { API_JSON.decodeFromString<Items<Apartment>>(it).items }.orEmpty()
    }
    override suspend fun saveFavorites(items: List<Apartment>, revision: Long) = favoritesMutex.withLock {
        if (revision < favoritesRevision) return@withLock
        withContext(Dispatchers.IO) { files.write(record("favorites"), API_JSON.encodeToString(Items(items))) }
        favoritesRevision = revision
    }
    override suspend fun pending(): PendingTurn? = withContext(Dispatchers.IO) {
        files.read(record("pending"))?.let { API_JSON.decodeFromString<PendingTurn>(it) }
    }
    override suspend fun savePending(turn: PendingTurn) = pendingMutex.withLock {
        withContext(Dispatchers.IO) { files.write(record("pending"), API_JSON.encodeToString(turn)) }
    }
    override suspend fun clearPending(expectedClientTurnId: String?) = pendingMutex.withLock {
        withContext(Dispatchers.IO) {
            val key = record("pending")
            val stored = files.read(key)?.let { API_JSON.decodeFromString<PendingTurn>(it) }
            if (expectedClientTurnId == null || stored?.clientTurnId == expectedClientTurnId) files.delete(key)
        }
    }
    override suspend fun pendingConversation(): PendingConversation? = withContext(Dispatchers.IO) {
        files.read(record("conversation"))?.let { API_JSON.decodeFromString<PendingConversation>(it) }
    }
    override suspend fun savePendingConversation(request: PendingConversation) = pendingMutex.withLock {
        withContext(Dispatchers.IO) { files.write(record("conversation"), API_JSON.encodeToString(request)) }
    }
    override suspend fun clearPendingConversation(expectedClientConversationId: String?) = pendingMutex.withLock {
        withContext(Dispatchers.IO) {
            val key = record("conversation")
            val stored = files.read(key)?.let { API_JSON.decodeFromString<PendingConversation>(it) }
            if (expectedClientConversationId == null || stored?.clientConversationId == expectedClientConversationId) files.delete(key)
        }
    }

    override fun send(turn: PendingTurn): Flow<StreamUpdate> = api.stream(
        "v1/conversations/${identifier(turn.conversationId)}/turns", "POST",
        API_JSON.encodeToString(TurnBody(turn.clientTurnId, turn.message, turn.selectedListingIds)))
    override fun replay(turn: PendingTurn): Flow<StreamUpdate> {
        val id = turn.turnId ?: throw InvalidResponse()
        return api.stream("v1/conversations/${identifier(turn.conversationId)}/turns/${identifier(id)}/events?after=${turn.afterSequence.coerceIn(0, 10000)}", "GET")
    }

    override suspend fun deleteAccount() {
        val oldUser = userId ?: throw SessionExpired()
        // Clear account-scoped private records even if the caller is cancelled after server deletion.
        withContext(NonCancellable) {
            api.deleteAccount()
            withContext(Dispatchers.IO) { files.deleteUser(oldUser) }
        }
    }

    override suspend fun resetSession() {
        val oldUser = api.resetSession()
        withContext(Dispatchers.IO) { oldUser?.let { files.deleteUser(it) } }
        favoritesRevision = -1
    }
    override suspend fun generateRecoveryCode(): String = api.generateRecoveryCode()
    override suspend fun recoverAccount(code: String) {
        withContext(NonCancellable) {
            val oldUser = api.recoverAccount(code)
            withContext(Dispatchers.IO) {
                oldUser?.let { files.deleteUser(it) }
                api.currentUserId()?.let { recovered -> if (recovered != oldUser) files.deleteUser(recovered) }
            }
            favoritesRevision = -1
        }
    }

    companion object {
        private val repositories = mutableMapOf<String, MekenRepository>()
        /** The single-use refresh mutex belongs to the process/session, including multiple activities. */
        @Synchronized fun create(context: Context, baseUrl: String, allowInsecureLocalDebug: Boolean = false): MekenRepository {
            val parsed = baseUrl.toHttpUrl()
            require(parsed.isHttps || allowInsecureLocalDebug && parsed.host in setOf("10.0.2.2", "127.0.0.1", "localhost"))
            val endpoint = parsed.newBuilder().apply { if (!parsed.encodedPath.endsWith('/')) addPathSegment("") }.build().toString()
            repositories[endpoint]?.let { return it }
            val files = EncryptedFileStore(context.applicationContext, endpoint)
            return MekenRepository(ApiClient(endpoint, TokenVault(files), allowInsecureLocalDebug = allowInsecureLocalDebug), files)
                .also { repositories[endpoint] = it }
        }
        private fun identifier(value: String): String = UUID.fromString(value).toString()
    }
    @Serializable private data class CreateBody(
        val preferences: Preferences,
        @SerialName("client_conversation_id") val clientConversationId: String? = null,
    )
    @Serializable private data class TurnBody(
        @SerialName("client_turn_id") val clientTurnId: String,
        val message: String,
        @SerialName("selected_listing_ids") val selectedListingIds: List<String>,
    )
}

fun userMessage(error: Throwable): String = when (error) {
    is SessionExpired -> error.message.orEmpty()
    is ApiFailure -> error.userMessage
    is StorageFailure, is InvalidResponse, is StreamInterrupted -> error.message.orEmpty()
    else -> "Не удалось связаться с сервисом. Проверьте интернет и повторите попытку."
}
