package kz.unknown.meken.ui

import android.content.Context
import androidx.lifecycle.SavedStateHandle
import androidx.lifecycle.ViewModel
import androidx.lifecycle.ViewModelProvider
import androidx.lifecycle.createSavedStateHandle
import androidx.lifecycle.viewModelScope
import androidx.lifecycle.viewmodel.CreationExtras
import java.util.UUID
import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.AppConfig
import kz.unknown.meken.core.ChatMessage
import kz.unknown.meken.core.ConversationHistory
import kz.unknown.meken.core.ConversationSummary
import kz.unknown.meken.core.Preferences
import kz.unknown.meken.core.SearchState
import kz.unknown.meken.core.ServerEvent
import kz.unknown.meken.core.Verification
import kz.unknown.meken.data.MekenRepository
import kz.unknown.meken.data.MobileRepository
import kz.unknown.meken.data.PendingTurn
import kz.unknown.meken.data.PendingConversation
import kz.unknown.meken.data.SessionExpired
import kz.unknown.meken.data.StreamInterrupted
import kz.unknown.meken.data.userMessage
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Job
import kotlinx.coroutines.cancelAndJoin
import kotlinx.coroutines.flow.MutableStateFlow
import kotlinx.coroutines.flow.StateFlow
import kotlinx.coroutines.flow.asStateFlow
import kotlinx.coroutines.flow.update
import kotlinx.coroutines.launch

data class MekenUiState(
    val search: SearchState = SearchState(),
    val config: AppConfig? = null,
    val favorites: List<Apartment> = emptyList(),
    val conversations: List<ConversationSummary> = emptyList(),
    val isReady: Boolean = false,
    val isStarting: Boolean = false,
    val initializationError: String? = null,
    val favoriteError: String? = null,
    val favoritesOffline: Boolean = false,
    val selectedForComparison: Set<String> = emptySet(),
    val busyFavorites: Set<String> = emptySet(),
    val isDeleting: Boolean = false,
    val sessionExpired: Boolean = false,
    val busyConversations: Set<String> = emptySet(),
    val historyError: String? = null,
    val hasMoreHistory: Boolean = false,
    val isLoadingOlder: Boolean = false,
    val isRecovering: Boolean = false,
    val isGeneratingRecoveryCode: Boolean = false,
)

/** All screen work belongs to ViewModel lifecycle; callbacks from previous conversations are ignored. */
class MekenViewModel(private val repository: MobileRepository, private val savedState: SavedStateHandle) : ViewModel() {
    internal val catalogRepository: MekenRepository? get() = repository as? MekenRepository
    private var catalogSession = false
    private val mutableState = MutableStateFlow(MekenUiState())
    val state: StateFlow<MekenUiState> = mutableState.asStateFlow()
    private var conversationId: String? = savedState[CONVERSATION_KEY]
    private var pending: PendingTurn? = null
    private var pendingConversation: PendingConversation? = null
    private var lastSequence = 0
    private var generation = 0L
    private var accountGeneration = 0L
    private var activeJob: Job? = null
    private var initializationJob: Job? = null
    private val accountJobs = mutableSetOf<Job>()
    private var favoritesRevision = repository.nextFavoritesRevision()
    private var historyRevision = 0L
    private var nextHistoryBefore: String? = null
    private var olderHistoryJob: Job? = null

    private fun launchWork(block: suspend () -> Unit): Job = viewModelScope.launch { block() }.also { job ->
        accountJobs.add(job)
        job.invokeOnCompletion { accountJobs.remove(job) }
    }
    private fun current(value: Long): Boolean = generation == value && !state.value.isDeleting && !state.value.isRecovering
    private fun currentAccount(value: Long): Boolean = accountGeneration == value && !state.value.isDeleting && !state.value.isRecovering
    private fun saveConversation(id: String?) { conversationId = id; savedState[CONVERSATION_KEY] = id }
    private fun invalidate() {
        generation++
        activeJob?.cancel(); activeJob = null
        olderHistoryJob?.cancel(); olderHistoryJob = null
        mutableState.update { it.copy(isLoadingOlder = false) }
    }

    fun initializeCatalogSession() {
        catalogSession = true
        if (state.value.isStarting) return
        mutableState.update { it.copy(isStarting = true) }
        viewModelScope.launch {
            try {
                repository.bootstrap()
                val config = repository.config()
                mutableState.update { it.copy(config = config, isReady = true, isStarting = false, initializationError = null) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { mutableState.update { it.copy(isStarting = false, initializationError = userMessage(error)) } }
        }
    }

    fun initialize() {
        if (catalogSession) { initializeCatalogSession(); return }
        if (state.value.isStarting || state.value.isReady || state.value.isDeleting || state.value.isRecovering || state.value.sessionExpired) return
        val account = accountGeneration
        mutableState.update { it.copy(isStarting = true, initializationError = null) }
        initializationJob = launchWork {
            try {
                repository.bootstrap()
                val cached = runCatching { repository.cachedFavorites() }.getOrDefault(emptyList())
                if (!currentAccount(account)) return@launchWork
                mutableState.update { it.copy(favorites = cached, favoritesOffline = cached.isNotEmpty()) }
                val config = repository.config()
                val history = repository.conversations()
                val savedPending = repository.pending()
                val savedCreation = repository.pendingConversation()
                if (!currentAccount(account)) return@launchWork
                pendingConversation = savedCreation
                pending = savedPending ?: savedCreation?.firstTurn
                mutableState.update { it.copy(config = config, conversations = history) }
                val id = conversationId?.takeIf { saved -> history.any { it.id == saved } }
                    ?: savedPending?.conversationId?.takeIf { saved -> history.any { it.id == saved } }
                    ?: history.firstOrNull()?.id
                if (savedCreation != null) {
                    saveConversation(null)
                    nextHistoryBefore = null
                    mutableState.update { it.copy(search = SearchState(
                        preferences = savedCreation.preferences,
                        messages = listOf(ChatMessage(id = "${savedCreation.firstTurn.clientTurnId}:user", isUser = true, text = savedCreation.firstTurn.message)),
                        isPending = true,
                        notice = "Создание диалога прервалось. Проверьте ответ, чтобы продолжить сохранённый запрос."), hasMoreHistory = false) }
                } else if (id != null) {
                    val result = repository.history(id)
                    if (!currentAccount(account)) return@launchWork
                    installHistory(result)
                } else { saveConversation(null) }
                if (!currentAccount(account)) return@launchWork
                mutableState.update { it.copy(isReady = true) }
                loadFavorites()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) {
                if (currentAccount(account)) {
                    handleSessionExpired(error)
                    mutableState.update { it.copy(initializationError = userMessage(error), favoritesOffline = it.favorites.isNotEmpty()) }
                }
            } finally {
                if (accountGeneration == account) mutableState.update { it.copy(isStarting = false) }
            }
        }
    }

    fun send(text: String, selected: List<String> = emptyList()) {
        val message = text.trim()
        val selectedIds = selected.toList()
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering || state.value.search.isStreaming || message.isEmpty()) return
        if (message.codePointCount(0, message.length) > 2000 || selectedIds.size > 3 || selectedIds.any { runCatching { UUID.fromString(it) }.isFailure }) {
            mutableState.update { it.copy(search = it.search.copy(error = "Сообщение должно содержать до 2000 символов. Для сравнения выберите до трёх квартир.")) }
            return
        }
        if (pending != null) {
            mutableState.update { it.copy(search = it.search.copy(error = "Сначала проверьте ответ на предыдущий запрос или начните новый диалог.")) }
            return
        }
        invalidate()
        val requestGeneration = generation
        val preferences = snapshot(state.value.search.preferences)
        val clientConversationId = if (conversationId == null) UUID.randomUUID().toString() else null
        val turn = PendingTurn(conversationId = conversationId ?: clientConversationId!!, message = message, selectedListingIds = selectedIds)
        val creation = clientConversationId?.let { PendingConversation(it, preferences, turn) }
        pending = turn
        pendingConversation = creation
        lastSequence = 0
        mutableState.update { it.copy(search = it.search.copy(
            isStreaming = true, isPending = false, error = null, notice = null, suggestions = emptyList(), turnId = null,
            messages = it.search.messages + ChatMessage(id = "${turn.clientTurnId}:user", isUser = true, text = message))) }
        activeJob = launchWork {
            try {
                val prepared = prepareTurn(turn, creation, requestGeneration)
                consume(prepared, requestGeneration, replay = false)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { streamFailure(error, requestGeneration) }
        }
    }

    fun resume() {
        if (!state.value.isReady || state.value.search.isStreaming || state.value.isDeleting || state.value.isRecovering) return
        if (pending == null && !state.value.search.isPending) return
        val request = pending ?: conversationId?.let { id -> state.value.search.turnId?.let { turnId ->
            PendingTurn(conversationId = id, message = "", turnId = turnId, afterSequence = lastSequence)
        } } ?: return
        invalidate()
        val requestGeneration = generation
        val creation = pendingConversation
        mutableState.update { it.copy(search = it.search.copy(isStreaming = true, isPending = false, error = null)) }
        activeJob = launchWork {
            try {
                // Missing accepted/header means retry exactly the stored UUID/body, never invent another turn.
                val prepared = prepareTurn(request, creation, requestGeneration)
                consume(prepared, requestGeneration, replay = prepared.turnId != null)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { streamFailure(error, requestGeneration) }
        }
    }

    private suspend fun prepareTurn(turn: PendingTurn, creation: PendingConversation?, requestGeneration: Long): PendingTurn {
        creation?.let {
            repository.savePendingConversation(it) // UUID, original preferences and first turn precede creation POST.
            if (!current(requestGeneration)) throw CancellationException()
        }
        repository.savePending(turn)
        if (!current(requestGeneration)) throw CancellationException()
        if (creation == null) return turn
        historyRevision++
        val created = repository.createConversation(creation)
        if (!current(requestGeneration)) throw CancellationException()
        val bound = turn.copy(conversationId = created.id)
        pending = bound
        repository.savePending(bound) // The server ID is durable before posting the first turn.
        if (!current(requestGeneration)) throw CancellationException()
        saveConversation(created.id)
        repository.clearPendingConversation(creation.clientConversationId)
        if (!current(requestGeneration)) throw CancellationException()
        pendingConversation = null
        historyRevision++
        mutableState.update { it.copy(conversations = listOf(created) + it.conversations.filterNot { row -> row.id == created.id }) }
        return bound
    }

    private suspend fun consume(original: PendingTurn, requestGeneration: Long, replay: Boolean) {
        var request = original
        var ended = false
        var serverPending = false
        val flow = if (replay) repository.replay(request) else repository.send(request)
        flow.collect { update ->
            if (!current(requestGeneration)) throw CancellationException()
            val event = update.event
            if (event is ServerEvent.Accepted) {
                // Server history already rendered this turn after process recreation.
                if (state.value.search.turnId != event.turnId) lastSequence = 0
                request = request.copy(turnId = event.turnId, afterSequence = lastSequence)
                pending = request
                repository.savePending(request)
                if (!current(requestGeneration)) throw CancellationException()
                mutableState.update { it.copy(search = it.search.copy(messages = it.search.messages.map { message ->
                    if (message.id == "${request.clientTurnId}:user") message.copy(id = "${event.turnId}:user") else message
                })) }
            }
            val sequence = update.sequence
            if (sequence != null && sequence <= lastSequence) {
                if (event is ServerEvent.Done) { ended = true; pending = null; repository.clearPending(request.clientTurnId) }
                return@collect
            }
            if (sequence != null) lastSequence = sequence
            mutableState.update { ui ->
                val search = if (event is ServerEvent.Message && sequence != null && request.turnId != null)
                    ui.search.copy(messages = (ui.search.messages + ChatMessage(id = "${request.turnId}:$sequence", isUser = false, text = event.text, citations = event.citations)).distinctBy { it.id })
                else ui.search.apply(event)
                ui.copy(search = search)
            }
            request = request.copy(afterSequence = lastSequence)
            if (event is ServerEvent.Done) {
                ended = true
                pending = null
                repository.clearPending(request.clientTurnId)
            } else {
                pending = request
                repository.savePending(request)
            }
            if (event is ServerEvent.Pending) serverPending = true
        }
        if (!current(requestGeneration)) return
        if (!ended && !serverPending) throw StreamInterrupted()
        mutableState.update { it.copy(search = it.search.copy(isStreaming = false, status = "",
            notice = if (serverPending) "Подбор ещё обрабатывается. Проверьте ответ немного позже." else it.search.notice)) }
        refreshHistory()
    }

    private fun streamFailure(error: Throwable, requestGeneration: Long) {
        if (!current(requestGeneration)) return
        handleSessionExpired(error)
        mutableState.update { it.copy(search = it.search.copy(isStreaming = false, status = "", isPending = pending != null, error = userMessage(error))) }
    }

    fun stop() {
        invalidate()
        mutableState.update { it.copy(search = it.search.copy(isStreaming = false, status = "", isPending = pending != null)) }
    }

    fun newConversation() {
        if (state.value.isDeleting || state.value.isRecovering) return
        stop()
        val previousCity = state.value.search.preferences.city
        val previousPending = pending
        val previousCreation = pendingConversation
        saveConversation(null)
        pending = null
        pendingConversation = null
        lastSequence = 0
        nextHistoryBefore = null
        mutableState.update { it.copy(search = SearchState(preferences = Preferences(city = previousCity)), selectedForComparison = emptySet(), hasMoreHistory = false, isLoadingOlder = false) }
        previousPending?.let { turn -> launchWork { runCatching { repository.clearPending(turn.clientTurnId) } } }
        previousCreation?.let { request -> launchWork { runCatching { repository.clearPendingConversation(request.clientConversationId) } } }
    }

    fun applyFilters(preferences: Preferences) {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering || state.value.search.isStreaming) return
        if (pending != null) {
            mutableState.update { it.copy(search = it.search.copy(error = "Сначала проверьте ответ на предыдущий запрос или начните новый диалог.")) }
            return
        }
        val requestedPreferences = snapshot(preferences)
        if (conversationId == null) {
            mutableState.update { it.copy(search = it.search.copy(preferences = requestedPreferences)) }
            send("Покажи квартиры по моим условиям")
            return
        }
        invalidate()
        val requestGeneration = generation
        val id = conversationId
        mutableState.update { it.copy(search = it.search.copy(isStreaming = true, status = "Применяем условия…", error = null)) }
        activeJob = launchWork {
            try {
                id?.let { repository.updatePreferences(it, requestedPreferences) }
                if (current(requestGeneration)) {
                    mutableState.update { it.copy(search = it.search.copy(preferences = requestedPreferences, isStreaming = false, status = "")) }
                    send("Покажи квартиры по моим условиям")
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { streamFailure(error, requestGeneration) }
        }
    }

    fun restoreConversation(id: String) {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering) return
        stop()
        val requestGeneration = generation
        mutableState.update { it.copy(search = it.search.copy(isStreaming = true, status = "Загружаем диалог…", error = null)) }
        activeJob = launchWork {
            try {
                val result = repository.history(id)
                if (current(requestGeneration)) installHistory(result)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { streamFailure(error, requestGeneration) }
        }
    }

    private fun installHistory(history: ConversationHistory) {
        pendingConversation?.let { old ->
            pendingConversation = null
            launchWork { runCatching { repository.clearPendingConversation(old.clientConversationId) } }
        }
        pending?.takeIf { it.conversationId != history.id }?.let { old ->
            pending = null
            launchWork { runCatching { repository.clearPending(old.clientTurnId) } }
        }
        var search = SearchState(preferences = history.preferences)
        history.turns.forEach { turn ->
            search = search.copy(messages = search.messages + ChatMessage(id = "${turn.id}:user", isUser = true, text = turn.message))
            turn.events.sortedBy { it.sequence }.forEach { event ->
                val decoded = event.streamEvent()
                search = if (decoded is ServerEvent.Message) search.copy(messages = search.messages + ChatMessage(
                    id = "${turn.id}:${event.sequence}", isUser = false, text = decoded.text, citations = decoded.citations))
                else search.apply(decoded)
            }
        }
        val last = history.turns.lastOrNull()
        lastSequence = last?.events?.maxOfOrNull { it.sequence } ?: 0
        val stored = pending?.takeIf { it.conversationId == history.id }
        if (stored != null && stored.turnId != null && stored.turnId == last?.id) {
            if (last.status in listOf("complete", "interrupted", "failed")) {
                pending = null
                launchWork { runCatching { repository.clearPending(stored.clientTurnId) } }
            } else pending = stored.copy(afterSequence = lastSequence)
        }
        search = search.copy(preferences = history.preferences, isStreaming = false, status = "", turnId = last?.id,
            isPending = last != null && last.status !in listOf("complete", "interrupted", "failed") || stored?.turnId == null && stored != null,
            notice = if (history.turns.isNotEmpty()) "Предыдущая подборка сохранена. Перед выбором уточните актуальное наличие." else null)
        nextHistoryBefore = history.nextBefore
        saveConversation(history.id)
        mutableState.update { it.copy(search = search, selectedForComparison = emptySet(), hasMoreHistory = history.hasMore && history.nextBefore != null, isLoadingOlder = false) }
    }

    fun loadOlderHistory() {
        val current = state.value
        if (!current.isReady || current.isDeleting || current.isRecovering || current.search.isStreaming || current.isLoadingOlder || !current.hasMoreHistory) return
        val id = conversationId ?: return
        val before = nextHistoryBefore ?: return
        val requestGeneration = generation
        mutableState.update { it.copy(isLoadingOlder = true, search = it.search.copy(error = null)) }
        olderHistoryJob = launchWork {
            try {
                val page = repository.history(id, before)
                if (!this@MekenViewModel.current(requestGeneration) || conversationId != id) return@launchWork
                if (page.id != id || page.hasMore && (page.nextBefore == null || page.nextBefore == before)) throw kz.unknown.meken.data.InvalidResponse()
                val older = page.turns.flatMap { turn ->
                    listOf(ChatMessage(id = "${turn.id}:user", isUser = true, text = turn.message)) +
                        turn.events.sortedBy { it.sequence }.mapNotNull { event ->
                            (event.streamEvent() as? ServerEvent.Message)?.let { message ->
                                ChatMessage(id = "${turn.id}:${event.sequence}", isUser = false, text = message.text, citations = message.citations)
                            }
                        }
                }.distinctBy { it.id }
                nextHistoryBefore = page.nextBefore
                mutableState.update { ui ->
                    val existing = ui.search.messages.mapTo(mutableSetOf()) { it.id }
                    ui.copy(search = ui.search.copy(messages = older.filterNot { it.id in existing } + ui.search.messages),
                        hasMoreHistory = page.hasMore && page.nextBefore != null)
                }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) {
                if (this@MekenViewModel.current(requestGeneration)) {
                    handleSessionExpired(error)
                    mutableState.update { it.copy(search = it.search.copy(error = userMessage(error))) }
                }
            } finally {
                if (generation == requestGeneration) mutableState.update { it.copy(isLoadingOlder = false) }
            }
        }
    }

    fun refreshHistory() {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering) return
        val account = accountGeneration
        val revision = historyRevision
        launchWork {
            try { val items = repository.conversations(); if (currentAccount(account) && revision == historyRevision) mutableState.update { it.copy(conversations = items) } }
            catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (currentAccount(account)) { handleSessionExpired(error); mutableState.update { it.copy(search = it.search.copy(error = userMessage(error))) } } }
        }
    }

    fun loadFavorites() {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering) return
        val account = accountGeneration
        val revision = favoritesRevision
        launchWork {
            try {
                val items = repository.favorites()
                if (!currentAccount(account) || revision != favoritesRevision) return@launchWork
                repository.saveFavorites(items, revision)
                if (currentAccount(account) && revision == favoritesRevision) mutableState.update { it.copy(favorites = items, favoritesOffline = false, favoriteError = null) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) {
                if (currentAccount(account)) {
                    handleSessionExpired(error)
                    mutableState.update { it.copy(favoritesOffline = true, favoriteError = userMessage(error) + " Сохранённые предложения могут измениться.") }
                }
            }
        }
    }

    fun toggleFavorite(apartment: Apartment) {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering || apartment.id in state.value.busyFavorites) return
        val account = accountGeneration
        favoritesRevision = repository.nextFavoritesRevision()
        val remove = state.value.favorites.any { it.id == apartment.id }
        mutableState.update { it.copy(busyFavorites = it.busyFavorites + apartment.id) }
        launchWork {
            try {
                repository.favorite(apartment.id, remove)
                if (!currentAccount(account)) return@launchWork
                val updated = if (remove) state.value.favorites.filterNot { it.id == apartment.id }
                    else listOf(apartment) + state.value.favorites.filterNot { it.id == apartment.id }
                favoritesRevision = repository.nextFavoritesRevision()
                val revision = favoritesRevision
                mutableState.update { it.copy(favorites = updated, favoriteError = null) }
                repository.saveFavorites(updated, revision)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (currentAccount(account)) { handleSessionExpired(error); mutableState.update { it.copy(favoriteError = userMessage(error)) } } }
            finally { if (accountGeneration == account) mutableState.update { it.copy(busyFavorites = it.busyFavorites - apartment.id) } }
        }
    }

    fun toggleComparison(id: String) {
        mutableState.update {
            val selected = it.selectedForComparison
            it.copy(selectedForComparison = if (id in selected) selected - id else if (selected.size < 3) selected + id else selected)
        }
    }
    fun compare() { if (state.value.selectedForComparison.size >= 2) send("Сравни выбранные квартиры", state.value.selectedForComparison.sorted()) }
    fun explain(apartment: Apartment) = send("Почему мне подходит эта квартира?", listOf(apartment.id))

    fun verify(apartment: Apartment, onResult: (Verification?, String?) -> Unit) {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering) { onResult(null, "Нет связи с сервисом. Повторите подключение."); return }
        val account = accountGeneration
        launchWork {
            try {
                val result = repository.verify(apartment.id)
                if (!currentAccount(account)) return@launchWork
                result.listing?.let { fresh ->
                    favoritesRevision = repository.nextFavoritesRevision()
                    mutableState.update { current -> current.copy(
                        favorites = current.favorites.map { if (it.id == fresh.id) fresh else it },
                        search = current.search.copy(apartments = current.search.apartments.map { if (it.id == fresh.id) fresh else it }.filter { it.status == "available" })) }
                    repository.saveFavorites(state.value.favorites, favoritesRevision)
                }
                if (currentAccount(account)) onResult(result, null)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (currentAccount(account)) { handleSessionExpired(error); onResult(null, userMessage(error)) } }
        }
    }

    fun deleteAccount(onResult: ((String?) -> Unit)? = null) {
        if (state.value.isDeleting || state.value.isRecovering || !state.value.isReady) return
        stop()
        accountGeneration++
        val toCancel = accountJobs.toList()
        mutableState.update { it.copy(isDeleting = true, isGeneratingRecoveryCode = false, busyFavorites = emptySet()) }
        viewModelScope.launch {
            try {
                toCancel.forEach { it.cancelAndJoin() }
                repository.deleteAccount()
                pending = null
                pendingConversation = null
                nextHistoryBefore = null
                saveConversation(null)
                lastSequence = 0
                mutableState.value = MekenUiState()
                onResult?.invoke(null)
                initialize()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { mutableState.update { it.copy(isDeleting = false, search = it.search.copy(error = userMessage(error))) }; handleSessionExpired(error); onResult?.invoke(userMessage(error)) }
        }
    }

    fun deleteConversation(id: String) {
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering || id in state.value.busyConversations) return
        val account = accountGeneration
        historyRevision++
        mutableState.update { it.copy(busyConversations = it.busyConversations + id, historyError = null) }
        launchWork {
            try {
                repository.deleteConversation(id)
                if (!currentAccount(account)) return@launchWork
                historyRevision++
                if (conversationId == id) newConversation()
                mutableState.update { it.copy(conversations = it.conversations.filterNot { row -> row.id == id }) }
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (currentAccount(account)) { handleSessionExpired(error); mutableState.update { it.copy(historyError = userMessage(error)) } } }
            finally { if (accountGeneration == account) mutableState.update { it.copy(busyConversations = it.busyConversations - id) } }
        }
    }

    fun resetSession() {
        if (!state.value.sessionExpired || state.value.isStarting || state.value.isDeleting || state.value.isRecovering) return
        stop()
        accountGeneration++
        val toCancel = accountJobs.toList()
        mutableState.update { it.copy(isStarting = true) }
        viewModelScope.launch {
            try {
                toCancel.forEach { it.cancelAndJoin() }
                repository.resetSession()
                pending = null
                pendingConversation = null
                nextHistoryBefore = null
                saveConversation(null)
                lastSequence = 0
                favoritesRevision = repository.nextFavoritesRevision()
                mutableState.value = MekenUiState()
                initialize()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { mutableState.update { it.copy(isStarting = false, initializationError = userMessage(error)) } }
        }
    }

    private fun handleSessionExpired(error: Throwable) {
        if (error !is SessionExpired) return
        accountGeneration++
        invalidate()
        pending = null
        pendingConversation = null
        nextHistoryBefore = null
        saveConversation(null)
        mutableState.value = MekenUiState(initializationError = userMessage(error), sessionExpired = true)
    }

    fun generateRecoveryCode(onResult: (String?, String?) -> Unit) {
        if (state.value.isGeneratingRecoveryCode) {
            onResult(null, "Код уже создаётся. Дождитесь завершения запроса.")
            return
        }
        if (!state.value.isReady || state.value.isDeleting || state.value.isRecovering ||
            state.value.config?.capabilities?.contains("account_recovery") != true) {
            onResult(null, "Восстановление аккаунта пока недоступно. Попробуйте позже.")
            return
        }
        val account = accountGeneration
        mutableState.update { it.copy(isGeneratingRecoveryCode = true) }
        launchWork {
            try {
                val code = repository.generateRecoveryCode()
                if (currentAccount(account)) onResult(code, null)
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) { if (currentAccount(account)) { handleSessionExpired(error); onResult(null, userMessage(error)) } }
            finally { if (accountGeneration == account) mutableState.update { it.copy(isGeneratingRecoveryCode = false) } }
        }
    }

    fun recoverAccount(code: String, onResult: (String?) -> Unit) {
        if (state.value.isDeleting || state.value.isRecovering) return
        val normalized = code.trim()
        if (!normalized.matches(Regex("[A-Za-z0-9_-]{43}"))) {
            onResult("Неверный код восстановления. Проверьте код и попробуйте снова.")
            return
        }
        stop()
        accountGeneration++
        val toCancel = accountJobs.toList()
        mutableState.update { it.copy(isRecovering = true, isGeneratingRecoveryCode = false, busyFavorites = emptySet(), busyConversations = emptySet()) }
        viewModelScope.launch {
            try {
                toCancel.forEach { it.cancelAndJoin() }
                repository.recoverAccount(normalized)
                pending = null
                pendingConversation = null
                nextHistoryBefore = null
                saveConversation(null)
                lastSequence = 0
                favoritesRevision = repository.nextFavoritesRevision()
                historyRevision++
                mutableState.value = MekenUiState()
                onResult(null)
                initialize()
            } catch (cancelled: CancellationException) { throw cancelled }
            catch (error: Throwable) {
                mutableState.update { it.copy(isRecovering = false, isStarting = false, search = it.search.copy(error = userMessage(error))) }
                onResult(userMessage(error))
            }
        }
    }

    companion object {
        private const val CONVERSATION_KEY = "active_conversation_id"
        private fun snapshot(preferences: Preferences) = preferences.copy(
            rooms = preferences.rooms.toList(),
            preferredAmenities = preferences.preferredAmenities.toList(),
            requiredAmenities = preferences.requiredAmenities.toList(),
        )
        fun factory(context: Context, baseUrl: String, allowInsecureLocalDebug: Boolean = false): ViewModelProvider.Factory = object : ViewModelProvider.Factory {
            override fun <T : ViewModel> create(modelClass: Class<T>, extras: CreationExtras): T {
                require(modelClass.isAssignableFrom(MekenViewModel::class.java))
                @Suppress("UNCHECKED_CAST")
                return MekenViewModel(MekenRepository.create(context, baseUrl, allowInsecureLocalDebug), extras.createSavedStateHandle()) as T
            }
        }
    }
}
