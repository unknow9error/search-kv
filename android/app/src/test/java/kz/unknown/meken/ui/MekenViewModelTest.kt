@file:OptIn(kotlinx.coroutines.ExperimentalCoroutinesApi::class)

package kz.unknown.meken.ui

import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModelStore
import kotlin.coroutines.Continuation
import kotlin.coroutines.resume
import kotlin.coroutines.suspendCoroutine
import kz.unknown.meken.core.API_JSON
import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.ConversationHistory
import kz.unknown.meken.core.ConversationSummary
import kz.unknown.meken.core.HistoryEvent
import kz.unknown.meken.core.HistoryTurn
import kz.unknown.meken.core.Items
import kz.unknown.meken.core.Preferences
import kz.unknown.meken.core.ServerEvent
import kz.unknown.meken.core.Verification
import kz.unknown.meken.data.MobileRepository
import kz.unknown.meken.data.ApiFailure
import kz.unknown.meken.data.PendingConversation
import kz.unknown.meken.data.PendingTurn
import kz.unknown.meken.data.SessionExpired
import kz.unknown.meken.data.StreamInterrupted
import kz.unknown.meken.data.StreamUpdate
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.awaitCancellation
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.flow
import kotlinx.coroutines.flow.flowOf
import kotlinx.coroutines.test.StandardTestDispatcher
import kotlinx.coroutines.test.TestDispatcher
import kotlinx.coroutines.test.UnconfinedTestDispatcher
import kotlinx.coroutines.test.resetMain
import kotlinx.coroutines.test.runCurrent
import kotlinx.coroutines.test.runTest
import kotlinx.coroutines.test.setMain
import kotlinx.serialization.json.JsonArray
import kotlinx.serialization.json.buildJsonObject
import kotlinx.serialization.json.encodeToJsonElement
import kotlinx.serialization.json.jsonObject
import kotlinx.serialization.json.put
import org.junit.After
import org.junit.Assert.*
import org.junit.Before
import org.junit.Test

/** Lifecycle and stale-response regressions use an in-memory boundary, never production data. */
class MekenViewModelTest {
    private lateinit var dispatcher: TestDispatcher
    private val models = ViewModelStore()
    private var modelCount = 0

    @Before fun setUp() {
        dispatcher = StandardTestDispatcher()
        Dispatchers.setMain(dispatcher)
    }

    @After fun tearDown() {
        models.clear()
        dispatcher.scheduler.runCurrent()
        Dispatchers.resetMain()
    }

    @Test fun `disconnect before accepted retries exactly the persisted request`() = runTest(dispatcher) {
        val repository = FakeRepository().apply {
            sendHandler = { _, attempt ->
                if (attempt == 1) flow { throw StreamInterrupted() }
                else completedResponse("Ответ после повторного подключения")
            }
        }
        val model = ready(repository)
        val selected = mutableListOf(LISTING_ONE, LISTING_TWO)

        model.send("  Сравни выбранные квартиры  ", selected)
        // The selection is captured before coroutine scheduling or conversation creation can suspend.
        selected.clear()
        selected.add(LISTING_THREE)
        runCurrent()

        val original = repository.posts.single()
        assertEquals(original, repository.pendingTurn)
        assertEquals("Сравни выбранные квартиры", original.message)
        assertEquals(listOf(LISTING_ONE, LISTING_TWO), original.selectedListingIds)
        assertNull(original.turnId)
        assertTrue(model.state.value.search.isPending)
        assertFalse(model.state.value.search.isStreaming)

        // A caller changing its comparison selection cannot mutate the durable request body.
        model.send("Другой запрос")
        runCurrent()
        assertEquals(1, repository.posts.size)

        model.resume()
        runCurrent()

        assertEquals(listOf(original, original), repository.posts)
        assertEquals(0, repository.replayCalls)
        assertNull(repository.pendingTurn)
        assertEquals(listOf("Сравни выбранные квартиры", "Ответ после повторного подключения"),
            model.state.value.search.messages.map { it.text })
        assertFalse(model.state.value.search.isPending)
        assertFalse(model.state.value.search.isStreaming)
    }

    @Test fun `late history cannot replace a newer conversation`() = runTest(dispatcher) {
        val repository = FakeRepository()
        val savedState = SavedStateHandle()
        val model = ready(repository, savedState)
        var delayedHistory: Continuation<ConversationHistory>? = null
        repository.historyHandler = { id, _ ->
            if (id == OLD_CONVERSATION) suspendCoroutine { delayedHistory = it }
            else emptyHistory(id, Preferences(city = "Астана"))
        }

        model.restoreConversation(OLD_CONVERSATION)
        runCurrent()
        assertNotNull(delayedHistory)
        assertTrue(model.state.value.search.isStreaming)

        model.newConversation()
        model.send("Новый поиск")
        runCurrent()
        val currentSearch = model.state.value.search
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))

        // Plain continuation deliberately completes even after its caller was cancelled.
        delayedHistory!!.resume(emptyHistory(OLD_CONVERSATION, Preferences(city = "Алматы", budgetMax = 1)))
        runCurrent()

        assertEquals(currentSearch, model.state.value.search)
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
        assertEquals(listOf("Новый поиск", "Текущий ответ"), model.state.value.search.messages.map { it.text })
    }

    @Test fun `busy filters exclude sends and late preference update cannot affect new search`() = runTest(dispatcher) {
        val repository = FakeRepository()
        val savedState = SavedStateHandle()
        val model = ready(repository, savedState)
        var delayedPreferences: Continuation<Preferences>? = null
        repository.updateHandler = { _, _ -> suspendCoroutine { delayedPreferences = it } }
        val oldPreferences = Preferences(city = "Алматы", budgetMax = 20_000_000)

        model.applyFilters(oldPreferences)
        model.send("Запрос во время применения условий")
        runCurrent()

        assertTrue(model.state.value.search.isStreaming)
        assertEquals(listOf(INITIAL_CONVERSATION to oldPreferences), repository.preferenceUpdates)
        assertTrue(repository.posts.isEmpty())

        model.stop()
        model.newConversation()
        val newPreferences = Preferences(city = "Шымкент", budgetMax = 45_000_000, rooms = listOf(2))
        model.applyFilters(newPreferences)
        runCurrent()
        assertEquals(1, repository.posts.size)
        assertEquals(newPreferences, model.state.value.search.preferences)
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
        val currentSearch = model.state.value.search

        delayedPreferences!!.resume(oldPreferences)
        runCurrent()

        assertEquals(1, repository.posts.size)
        assertEquals(currentSearch, model.state.value.search)
        assertEquals(newPreferences, model.state.value.search.preferences)
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
    }

    @Test fun `filters without a conversation leave stop owning the running stream`() = runTest(dispatcher) {
        // Main.immediate executes launch bodies before assignment returns. This reproduces the
        // production reentrancy that a queued StandardTestDispatcher alone cannot exercise.
        Dispatchers.setMain(UnconfinedTestDispatcher(testScheduler))
        var started = false
        var cancelled = false
        val repository = FakeRepository().apply {
            conversationRows = emptyList()
            sendHandler = { _, _ -> flow {
                try {
                    emit(StreamUpdate(null, ServerEvent.Accepted(TURN_ID)))
                    started = true
                    awaitCancellation()
                } finally { cancelled = true }
            } }
        }
        val model = ready(repository)

        model.applyFilters(Preferences(city = "Астана", rooms = listOf(1)))
        runCurrent()
        assertTrue(started)
        assertTrue(model.state.value.search.isStreaming)
        assertFalse(cancelled)

        model.stop()
        runCurrent()

        assertTrue(cancelled)
        assertFalse(model.state.value.search.isStreaming)
        assertTrue(model.state.value.search.isPending)
        assertEquals(TURN_ID, repository.pendingTurn?.turnId)
        assertEquals(1, repository.posts.size)
    }

    @Test fun `expired session cannot silently enroll until explicit reset`() = runTest(dispatcher) {
        val repository = FakeRepository().apply { bootstrapError = SessionExpired() }
        val model = createModel(repository, SavedStateHandle())

        model.initialize()
        runCurrent()
        assertTrue(model.state.value.sessionExpired)
        assertFalse(model.state.value.isReady)
        assertEquals(1, repository.bootstrapCalls)

        model.initialize()
        model.initialize()
        model.send("Новый поиск")
        runCurrent()
        assertEquals(1, repository.bootstrapCalls)
        assertEquals(0, repository.resetCalls)
        assertTrue(repository.posts.isEmpty())

        model.resetSession()
        runCurrent()

        assertEquals(1, repository.resetCalls)
        assertEquals(2, repository.bootstrapCalls)
        assertTrue(model.state.value.isReady)
        assertFalse(model.state.value.sessionExpired)
        assertFalse(model.state.value.isStarting)
    }

    @Test fun `lost conversation response retries immutable creation and original first turn`() = runTest(dispatcher) {
        val repository = FakeRepository().apply {
            conversationRows = emptyList()
            createHandler = { request, attempt ->
                if (attempt == 1) throw StreamInterrupted()
                ConversationSummary(NEW_CONVERSATION, "Новый поиск", request.preferences)
            }
        }
        val model = ready(repository)
        val rooms = mutableListOf(2)
        val amenities = mutableListOf("school")
        val preferences = Preferences(city = "Астана", budgetMax = 45_000_000,
            rooms = rooms, requiredAmenities = amenities)

        model.applyFilters(preferences)
        runCurrent()

        val original = repository.creations.single()
        assertEquals(original, repository.pendingCreation)
        assertTrue(model.state.value.search.isPending)
        assertFalse(model.state.value.search.isStreaming)
        assertTrue(repository.posts.isEmpty())
        assertEquals("Покажи квартиры по моим условиям", original.firstTurn.message)

        rooms += 3
        amenities += "park"
        model.applyFilters(Preferences(city = "Алматы", rooms = listOf(1)))
        model.send("Замени исходный запрос")
        runCurrent()

        assertEquals(listOf(2), original.preferences.rooms)
        assertEquals(listOf("school"), original.preferences.requiredAmenities)
        assertEquals(1, repository.creations.size)
        assertTrue(repository.posts.isEmpty())

        model.resume()
        runCurrent()

        assertEquals(listOf(original, original), repository.creations)
        assertNotEquals(original.clientConversationId, NEW_CONVERSATION)
        assertEquals(original.firstTurn.copy(conversationId = NEW_CONVERSATION), repository.posts.single())
        assertNull(repository.pendingCreation)
        assertNull(repository.pendingTurn)
        assertFalse(model.state.value.search.isPending)
        assertEquals(1, model.state.value.search.messages.count { it.isUser })
    }

    @Test fun `process recreation restores unresolved conversation creation before old history`() = runTest(dispatcher) {
        val clientConversationId = "d0bb3310-a8a3-42b2-88a5-b67f576af901"
        val original = PendingConversation(
            clientConversationId = clientConversationId,
            preferences = Preferences(city = "Шымкент", budgetMax = 30_000_000, rooms = listOf(1)),
            firstTurn = PendingTurn(conversationId = clientConversationId, clientTurnId = TURN_ID,
                message = "Первый запрос до закрытия приложения", selectedListingIds = listOf(LISTING_ONE, LISTING_TWO)),
        )
        val repository = FakeRepository().apply { pendingCreation = original }
        val savedState = SavedStateHandle(mapOf(CONVERSATION_KEY to INITIAL_CONVERSATION))
        val model = ready(repository, savedState)

        assertTrue(model.state.value.search.isPending)
        assertEquals(original.preferences, model.state.value.search.preferences)
        assertEquals(listOf(original.firstTurn.message), model.state.value.search.messages.map { it.text })
        assertTrue(repository.creations.isEmpty())
        assertTrue(repository.posts.isEmpty())

        model.resume()
        runCurrent()

        assertEquals(listOf(original), repository.creations)
        assertEquals(original.firstTurn.copy(conversationId = NEW_CONVERSATION), repository.posts.single())
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
        assertNull(repository.pendingCreation)
        assertEquals(1, model.state.value.search.messages.count { it.isUser })
    }

    @Test fun `late older history cannot prepend into a newer conversation`() = runTest(dispatcher) {
        val latest = emptyHistory(INITIAL_CONVERSATION, Preferences(city = "Астана")).copy(
            turns = listOf(completeTurn(LATEST_HISTORY_TURN, "Текущий запрос", "Текущий ответ")),
            hasMore = true, nextBefore = LATEST_HISTORY_TURN,
        )
        val repository = FakeRepository().apply { historyHandler = { _, _ -> latest } }
        val savedState = SavedStateHandle()
        val model = ready(repository, savedState)
        var olderPage: Continuation<ConversationHistory>? = null
        repository.historyHandler = { _, before ->
            assertEquals(LATEST_HISTORY_TURN, before)
            suspendCoroutine { olderPage = it }
        }

        model.loadOlderHistory()
        runCurrent()
        assertNotNull(olderPage)
        assertTrue(model.state.value.isLoadingOlder)

        model.newConversation()
        model.send("Новый поиск")
        runCurrent()
        val currentSearch = model.state.value.search
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))

        olderPage!!.resume(emptyHistory(INITIAL_CONVERSATION, Preferences(city = "Алматы")).copy(
            turns = listOf(completeTurn(OLDEST_HISTORY_TURN, "Старый запрос", "Старый ответ")),
        ))
        runCurrent()

        assertEquals(currentSearch, model.state.value.search)
        assertFalse(model.state.value.isLoadingOlder)
        assertFalse(model.state.value.hasMoreHistory)
        assertEquals(NEW_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
    }

    @Test fun `all older pages deduplicate overlap without changing latest search or replay position`() = runTest(dispatcher) {
        val preferences = Preferences(city = "Астана", budgetMax = 45_000_000)
        val latestApartment = apartment(LISTING_ONE, "Астана")
        val oldApartment = apartment(LISTING_TWO, "Алматы")
        val middle = completeTurn(MIDDLE_HISTORY_TURN, "Средний запрос", "Средний ответ")
        val oldest = completeTurn(OLDEST_HISTORY_TURN, "Самый старый запрос", "Самый старый ответ")
        val older = HistoryTurn(OLDER_HISTORY_TURN, "Старый запрос", "complete", listOf(
            HistoryEvent(10, "preferences", API_JSON.encodeToJsonElement(Preferences(city = "Алматы")).jsonObject),
            HistoryEvent(11, "listings", API_JSON.encodeToJsonElement(Items(listOf(oldApartment))).jsonObject),
            messageEvent(17, "Старый ответ"), doneEvent(18),
        ))
        val latest = HistoryTurn(LATEST_HISTORY_TURN, "Последний запрос", "running", listOf(
            HistoryEvent(1, "status", buildJsonObject { put("text", "Подбираем") }),
            HistoryEvent(2, "preferences", API_JSON.encodeToJsonElement(preferences).jsonObject),
            HistoryEvent(3, "listings", API_JSON.encodeToJsonElement(Items(listOf(latestApartment))).jsonObject),
            messageEvent(4, "Последний ответ"),
        ))
        val repository = FakeRepository().apply {
            pendingTurn = PendingTurn(conversationId = INITIAL_CONVERSATION, message = latest.message,
                turnId = LATEST_HISTORY_TURN, afterSequence = 0)
            historyHandler = { id, before ->
                when (before) {
                    null -> ConversationHistory(id, "Поиск", preferences, listOf(middle, latest), true, MIDDLE_HISTORY_TURN)
                    MIDDLE_HISTORY_TURN -> ConversationHistory(id, "Поиск", Preferences(city = "Алматы"), listOf(older, middle), true, OLDER_HISTORY_TURN)
                    OLDER_HISTORY_TURN -> ConversationHistory(id, "Поиск", Preferences(city = "Алматы"), listOf(oldest, older))
                    else -> error("Unexpected older-history cursor: $before")
                }
            }
            replayHandler = { flowOf(
                StreamUpdate(null, ServerEvent.Accepted(LATEST_HISTORY_TURN)),
                StreamUpdate(5, ServerEvent.Message("Продолжение последнего ответа", emptyList())),
                StreamUpdate(6, ServerEvent.Done("complete")),
            ) }
        }
        val model = ready(repository)
        val originalIds = model.state.value.search.messages.map { it.id }
        assertTrue(model.state.value.hasMoreHistory)

        model.loadOlderHistory()
        model.loadOlderHistory() // A repeated tap while busy must not fetch the same page twice.
        runCurrent()
        assertEquals(2, repository.historyRequests.size)
        assertTrue(model.state.value.hasMoreHistory)
        assertEquals(preferences, model.state.value.search.preferences)
        assertEquals(listOf(latestApartment), model.state.value.search.apartments)
        assertEquals(LATEST_HISTORY_TURN, model.state.value.search.turnId)

        model.loadOlderHistory()
        runCurrent()
        val search = model.state.value.search
        assertEquals(listOf(INITIAL_CONVERSATION to null, INITIAL_CONVERSATION to MIDDLE_HISTORY_TURN,
            INITIAL_CONVERSATION to OLDER_HISTORY_TURN), repository.historyRequests)
        assertFalse(model.state.value.hasMoreHistory)
        assertFalse(model.state.value.isLoadingOlder)
        assertEquals(listOf("Самый старый запрос", "Самый старый ответ", "Старый запрос", "Старый ответ",
            "Средний запрос", "Средний ответ", "Последний запрос", "Последний ответ"), search.messages.map { it.text })
        assertEquals(search.messages.size, search.messages.map { it.id }.toSet().size)
        assertEquals(originalIds, search.messages.takeLast(originalIds.size).map { it.id })
        assertEquals(preferences, search.preferences)
        assertEquals(listOf(latestApartment), search.apartments)
        assertEquals(LATEST_HISTORY_TURN, search.turnId)
        assertTrue(search.isPending)

        model.resume()
        runCurrent()
        assertEquals(4, repository.replays.single().afterSequence)
        assertEquals(LATEST_HISTORY_TURN, repository.replays.single().turnId)
        assertEquals("Продолжение последнего ответа", model.state.value.search.messages.last().text)
        assertFalse(model.state.value.search.isPending)
    }

    @Test fun `invalid recovery code preserves the active account search favorites and pending request`() = runTest(dispatcher) {
        val oldFavorite = apartment(LISTING_ONE, "Астана")
        val failure = ApiFailure(401, "invalid_recovery_code")
        val repository = FakeRepository().apply {
            favoriteRows = listOf(oldFavorite)
            sendHandler = { _, _ -> flow {
                emit(StreamUpdate(null, ServerEvent.Accepted(TURN_ID)))
                throw StreamInterrupted()
            } }
            recoveryHandler = { throw failure }
        }
        val savedState = SavedStateHandle()
        val model = ready(repository, savedState)
        model.send("Сохрани мой текущий поиск", listOf(LISTING_ONE))
        runCurrent()
        val originalState = model.state.value
        val originalPending = repository.pendingTurn
        val originalUser = repository.userId
        val results = mutableListOf<String?>()
        assertNotNull(originalPending)
        assertTrue(originalState.search.isPending)

        model.recoverAccount("  $RECOVERY_CODE \n") { results += it }
        runCurrent()

        assertEquals(listOf(RECOVERY_CODE), repository.recoveryCodes)
        assertEquals(listOf(failure.userMessage), results)
        assertEquals(originalUser, repository.userId)
        assertTrue(model.state.value.isReady)
        assertFalse(model.state.value.sessionExpired)
        assertFalse(model.state.value.isRecovering)
        assertEquals(originalState.conversations, model.state.value.conversations)
        assertEquals(originalState.favorites, model.state.value.favorites)
        assertEquals(originalState.search.copy(error = failure.userMessage), model.state.value.search)
        assertEquals(originalPending, repository.pendingTurn)
        assertEquals(INITIAL_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
        assertEquals(1, repository.bootstrapCalls)
        assertEquals(0, repository.resetCalls)
    }

    @Test fun `successful account recovery replaces old state and ignores its cancelled history`() = runTest(dispatcher) {
        val oldFavorite = apartment(LISTING_ONE, "Астана")
        val recoveredFavorite = apartment(LISTING_TWO, "Шымкент")
        val recoveredPreferences = Preferences(city = "Шымкент", budgetMax = 32_000_000)
        val recoveredHistory = ConversationHistory(RECOVERED_CONVERSATION, "Восстановленный поиск", recoveredPreferences,
            listOf(completeTurn(LATEST_HISTORY_TURN, "Сохранённый запрос другого устройства", "Ответ восстановленного аккаунта")))
        val repository = FakeRepository().apply {
            favoriteRows = listOf(oldFavorite)
            sendHandler = { _, _ -> flow { throw StreamInterrupted() } }
            recoveryHandler = {
                userId = "recovered-account"
                pendingTurn = null
                pendingCreation = null
                favoriteRows = listOf(recoveredFavorite)
                conversationRows = listOf(ConversationSummary(RECOVERED_CONVERSATION, "Восстановленный поиск", recoveredPreferences))
                historyHandler = { _, _ -> recoveredHistory }
            }
        }
        val savedState = SavedStateHandle()
        val model = ready(repository, savedState)
        model.send("Запрос старого аккаунта")
        runCurrent()
        model.toggleComparison(LISTING_ONE)
        assertNotNull(repository.pendingTurn)
        var delayedOldHistory: Continuation<ConversationHistory>? = null
        repository.historyHandler = { _, _ -> suspendCoroutine { delayedOldHistory = it } }
        model.restoreConversation(OLD_CONVERSATION)
        runCurrent()
        assertNotNull(delayedOldHistory)
        val results = mutableListOf<String?>()

        model.recoverAccount(RECOVERY_CODE) { results += it }
        runCurrent()
        assertTrue(model.state.value.isRecovering)
        assertTrue(results.isEmpty())
        // Recovery waits for cancelled work to finish. Its response must not install old history
        // during that transition before the recovered identity is bootstrapped.
        delayedOldHistory!!.resume(emptyHistory(OLD_CONVERSATION, Preferences(city = "Алматы")))
        runCurrent()

        assertEquals(listOf<String?>(null), results)
        assertEquals("recovered-account", repository.userId)
        assertTrue(model.state.value.isReady)
        assertFalse(model.state.value.isRecovering)
        assertFalse(model.state.value.sessionExpired)
        assertEquals(RECOVERED_CONVERSATION, savedState.get<String>(CONVERSATION_KEY))
        assertEquals(recoveredPreferences, model.state.value.search.preferences)
        assertEquals(listOf("Сохранённый запрос другого устройства", "Ответ восстановленного аккаунта"),
            model.state.value.search.messages.map { it.text })
        assertEquals(listOf(recoveredFavorite), model.state.value.favorites)
        assertTrue(model.state.value.selectedForComparison.isEmpty())
        assertFalse(model.state.value.search.isPending)
        assertNull(repository.pendingTurn)
        assertNull(repository.pendingCreation)
        assertEquals(2, repository.bootstrapCalls)
        assertEquals(0, repository.resetCalls)
        assertTrue(repository.creations.isEmpty())
        assertEquals(1, repository.posts.size)
    }

    @Test fun `overlapping recovery code generation makes one request then permits later generation`() = runTest(dispatcher) {
        var delayedCode: Continuation<String>? = null
        val repository = FakeRepository().apply {
            generateCodeHandler = { suspendCoroutine { delayedCode = it } }
        }
        val model = ready(repository)
        val firstResults = mutableListOf<Pair<String?, String?>>()
        val duplicateResults = mutableListOf<Pair<String?, String?>>()
        val laterResults = mutableListOf<Pair<String?, String?>>()

        model.generateRecoveryCode { code, error -> firstResults += code to error }
        assertTrue(model.state.value.isGeneratingRecoveryCode)
        runCurrent()
        assertEquals(1, repository.generationCalls)
        assertNotNull(delayedCode)
        assertTrue(firstResults.isEmpty())

        model.generateRecoveryCode { code, error -> duplicateResults += code to error }
        runCurrent()
        assertEquals(1, repository.generationCalls)
        assertNull(duplicateResults.single().first)
        assertTrue(duplicateResults.single().second.orEmpty().contains("Код уже создаётся"))
        assertTrue(model.state.value.isGeneratingRecoveryCode)
        assertTrue(firstResults.isEmpty())

        delayedCode!!.resume(RECOVERY_CODE)
        runCurrent()
        assertEquals(listOf(RECOVERY_CODE to null), firstResults)
        assertFalse(model.state.value.isGeneratingRecoveryCode)

        repository.generateCodeHandler = { RECOVERY_CODE }
        model.generateRecoveryCode { code, error -> laterResults += code to error }
        runCurrent()
        assertEquals(2, repository.generationCalls)
        assertEquals(listOf(RECOVERY_CODE to null), laterResults)
        assertFalse(model.state.value.isGeneratingRecoveryCode)
    }

    private fun createModel(repository: FakeRepository, savedState: SavedStateHandle): MekenViewModel =
        MekenViewModel(repository, savedState).also { models.put("model-${modelCount++}", it) }

    private fun ready(repository: FakeRepository, savedState: SavedStateHandle = SavedStateHandle()): MekenViewModel =
        createModel(repository, savedState).also {
            it.initialize()
            dispatcher.scheduler.runCurrent()
            assertTrue(it.state.value.isReady)
            assertFalse(it.state.value.isStarting)
        }

    private class FakeRepository : MobileRepository {
        override var userId = "isolated-test-account"
        private var revision = 0L
        var bootstrapCalls = 0
        var resetCalls = 0
        var replayCalls = 0
        var generationCalls = 0
        var bootstrapError: Throwable? = null
        var pendingTurn: PendingTurn? = null
        var pendingCreation: PendingConversation? = null
        var favoriteRows = emptyList<Apartment>()
        var conversationRows = listOf(ConversationSummary(INITIAL_CONVERSATION, "Первый поиск", Preferences(city = "Астана")))
        val posts = mutableListOf<PendingTurn>()
        val creations = mutableListOf<PendingConversation>()
        val replays = mutableListOf<PendingTurn>()
        val historyRequests = mutableListOf<Pair<String, String?>>()
        val recoveryCodes = mutableListOf<String>()
        val preferenceUpdates = mutableListOf<Pair<String, Preferences>>()
        var historyHandler: suspend (String, String?) -> ConversationHistory = { id, _ ->
            emptyHistory(id, conversationRows.firstOrNull { it.id == id }?.preferences ?: Preferences())
        }
        var createHandler: suspend (PendingConversation, Int) -> ConversationSummary = { request, _ ->
            ConversationSummary(NEW_CONVERSATION, "Новый поиск", request.preferences)
        }
        var updateHandler: suspend (String, Preferences) -> Preferences = { _, preferences -> preferences }
        var sendHandler: (PendingTurn, Int) -> Flow<StreamUpdate> = { _, _ -> completedResponse("Текущий ответ") }
        var replayHandler: (PendingTurn) -> Flow<StreamUpdate> = { completedResponse("Восстановленный ответ") }
        var recoveryHandler: suspend (String) -> Unit = {}
        var generateCodeHandler: suspend () -> String = { RECOVERY_CODE }

        override fun nextFavoritesRevision() = ++revision
        override suspend fun bootstrap() { bootstrapCalls++; bootstrapError?.let { throw it } }
        override suspend fun config() = AppConfig("production", true, listOf("Астана", "Алматы", "Шымкент"),
            retentionDays = 30, capabilities = listOf("account_recovery"))
        override suspend fun conversations() = conversationRows
        override suspend fun history(id: String, before: String?): ConversationHistory {
            historyRequests += id to before
            return historyHandler(id, before)
        }
        override suspend fun deleteConversation(id: String) { conversationRows = conversationRows.filterNot { it.id == id } }
        override suspend fun createConversation(request: PendingConversation): ConversationSummary {
            check(pendingCreation == request) { "Conversation creation must be durable before its first POST or retry" }
            creations += request
            return createHandler(request, creations.size).also {
                conversationRows = conversationRows.filterNot { existing -> existing.id == it.id } + it
            }
        }
        override suspend fun pendingConversation() = pendingCreation
        override suspend fun savePendingConversation(request: PendingConversation) { pendingCreation = request }
        override suspend fun clearPendingConversation(expectedClientConversationId: String?) {
            if (expectedClientConversationId == null || pendingCreation?.clientConversationId == expectedClientConversationId) pendingCreation = null
        }
        override suspend fun updatePreferences(id: String, preferences: Preferences): Preferences {
            preferenceUpdates += id to preferences
            return updateHandler(id, preferences)
        }
        override suspend fun favorites() = favoriteRows
        override suspend fun favorite(id: String, remove: Boolean) = Unit
        override suspend fun verify(id: String) = Verification(verification = "confirmed", checkedAt = "2026-10-04T00:00:00Z")
        override suspend fun cachedFavorites() = emptyList<Apartment>()
        override suspend fun saveFavorites(items: List<Apartment>, revision: Long) = Unit
        override suspend fun pending() = pendingTurn
        override suspend fun savePending(turn: PendingTurn) { pendingTurn = turn }
        override suspend fun clearPending(expectedClientTurnId: String?) {
            if (expectedClientTurnId == null || pendingTurn?.clientTurnId == expectedClientTurnId) pendingTurn = null
        }
        override fun send(turn: PendingTurn): Flow<StreamUpdate> {
            check(pendingTurn == turn) { "Request must be durable before its first POST or retry" }
            posts += turn
            return sendHandler(turn, posts.size)
        }
        override fun replay(turn: PendingTurn): Flow<StreamUpdate> {
            replayCalls++
            replays += turn
            return replayHandler(turn)
        }
        override suspend fun deleteAccount() = Unit
        override suspend fun resetSession() { resetCalls++; bootstrapError = null; pendingTurn = null; pendingCreation = null; conversationRows = emptyList() }
        override suspend fun generateRecoveryCode(): String {
            generationCalls++
            return generateCodeHandler()
        }
        override suspend fun recoverAccount(code: String) {
            recoveryCodes += code
            recoveryHandler(code)
        }
    }

    companion object {
        private const val CONVERSATION_KEY = "active_conversation_id"
        private const val INITIAL_CONVERSATION = "d0bb3310-a8a3-42b2-88a5-b67f576af100"
        private const val OLD_CONVERSATION = "d0bb3310-a8a3-42b2-88a5-b67f576af101"
        private const val NEW_CONVERSATION = "d0bb3310-a8a3-42b2-88a5-b67f576af102"
        private const val RECOVERED_CONVERSATION = "d0bb3310-a8a3-42b2-88a5-b67f576af103"
        private const val RECOVERY_CODE = "AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
        private const val TURN_ID = "d0bb3310-a8a3-42b2-88a5-b67f576af200"
        private const val OLDEST_HISTORY_TURN = "d0bb3310-a8a3-42b2-88a5-b67f576af210"
        private const val OLDER_HISTORY_TURN = "d0bb3310-a8a3-42b2-88a5-b67f576af211"
        private const val MIDDLE_HISTORY_TURN = "d0bb3310-a8a3-42b2-88a5-b67f576af212"
        private const val LATEST_HISTORY_TURN = "d0bb3310-a8a3-42b2-88a5-b67f576af213"
        private const val LISTING_ONE = "d0bb3310-a8a3-42b2-88a5-b67f576af301"
        private const val LISTING_TWO = "d0bb3310-a8a3-42b2-88a5-b67f576af302"
        private const val LISTING_THREE = "d0bb3310-a8a3-42b2-88a5-b67f576af303"
        private fun emptyHistory(id: String, preferences: Preferences) = ConversationHistory(id, "Сохранённый поиск", preferences, emptyList())
        private fun completeTurn(id: String, userText: String, answer: String) =
            HistoryTurn(id, userText, "complete", listOf(messageEvent(1, answer), doneEvent(2)))
        private fun messageEvent(sequence: Int, text: String) = HistoryEvent(sequence, "message", buildJsonObject {
            put("text", text)
            put("citations", JsonArray(emptyList()))
        })
        private fun doneEvent(sequence: Int) = HistoryEvent(sequence, "done", buildJsonObject { put("status", "complete") })
        private fun apartment(id: String, city: String) = Apartment(
            id = id, providerId = "provider", providerName = "Застройщик", complexName = "ЖК", city = city,
            district = "Центр", address = "Адрес", rooms = 2, areaM2 = 60.0, floor = 5, totalFloors = 10,
            priceKzt = 40_000_000, status = "available", finish = "черновая", completion = "сдан",
            sourceUrl = "https://example.com/listing", observedAt = "2026-10-04T00:00:00Z", version = 1,
            provenance = "provider", freshness = "recent", reasons = emptyList(), tradeoffs = emptyList(), amenities = emptyList(),
        )
        private fun completedResponse(text: String): Flow<StreamUpdate> = flowOf(
            StreamUpdate(null, ServerEvent.Accepted(TURN_ID)),
            StreamUpdate(1, ServerEvent.Message(text, emptyList())),
            StreamUpdate(2, ServerEvent.Done("complete")),
        )
    }
}
