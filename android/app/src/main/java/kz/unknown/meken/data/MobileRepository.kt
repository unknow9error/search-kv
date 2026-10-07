package kz.unknown.meken.data

import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.ConversationHistory
import kz.unknown.meken.core.ConversationSummary
import kz.unknown.meken.core.Preferences
import kz.unknown.meken.core.Verification
import kotlinx.coroutines.flow.Flow

/** Boundary between lifecycle-owned screens and account/network/storage operations. */
interface MobileRepository {
    val userId: String?
    fun nextFavoritesRevision(): Long
    suspend fun bootstrap()
    suspend fun config(): AppConfig
    suspend fun conversations(): List<ConversationSummary>
    suspend fun history(id: String, before: String? = null): ConversationHistory
    suspend fun deleteConversation(id: String)
    suspend fun createConversation(request: PendingConversation): ConversationSummary
    suspend fun updatePreferences(id: String, preferences: Preferences): Preferences
    suspend fun favorites(): List<Apartment>
    suspend fun favorite(id: String, remove: Boolean)
    suspend fun verify(id: String): Verification
    suspend fun cachedFavorites(): List<Apartment>
    suspend fun saveFavorites(items: List<Apartment>, revision: Long = 0)
    suspend fun pending(): PendingTurn?
    suspend fun savePending(turn: PendingTurn)
    suspend fun clearPending(expectedClientTurnId: String? = null)
    suspend fun pendingConversation(): PendingConversation?
    suspend fun savePendingConversation(request: PendingConversation)
    suspend fun clearPendingConversation(expectedClientConversationId: String? = null)
    fun send(turn: PendingTurn): Flow<StreamUpdate>
    fun replay(turn: PendingTurn): Flow<StreamUpdate>
    suspend fun deleteAccount()
    suspend fun resetSession()
    suspend fun generateRecoveryCode(): String
    suspend fun recoverAccount(code: String)
}
