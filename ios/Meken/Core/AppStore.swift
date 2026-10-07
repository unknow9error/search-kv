import Foundation
import Observation
import SwiftData
import MekenCore

@Model
final class CachedFavorite {
    @Attribute(.unique) var id: String
    var payload: Data
    var savedAt: Date
    var ownerKey: String = ""
    init(apartment: Apartment, ownerKey: String) throws {
        id = ownerKey + ":" + apartment.id
        self.ownerKey = ownerKey
        payload = try APIJSON.encoder().encode(apartment)
        savedAt = Date()
    }
}

@Observable @MainActor
final class AppStore {
    var search = SearchState()
    var config: AppConfig?
    var favorites: [Apartment] = []
    var conversations: [ConversationSummary] = []
    var conversationID: String?
    var initializationError: String?
    var favoriteError: String?
    var favoritesOffline = false
    var isReady = false
    var isStarting = false
    var isDeleting = false
    var needsConnectionRecovery = false
    var requiresSessionReset = false
    var selectedTab = 0
    var selectedForComparison = Set<String>()
    var isMutatingFavorite = Set<String>()
    private(set) var pendingRequest: PendingTurnRequest?
    private(set) var pendingConversation: PendingConversationRequest?
    private(set) var hasOlderMessages = false
    private(set) var isLoadingOlderMessages = false
    private(set) var historyLoadError: String?
    private(set) var isRestoringConversation = false
    private(set) var isApplyingFilters = false
    var isPreparingSearch: Bool { isStarting || isRestoringConversation || isApplyingFilters }
    var supportsAccountRecovery: Bool { config?.capabilities.contains("account_recovery") == true }
    var canOfferAccountRecovery: Bool { config == nil || supportsAccountRecovery }
    private var isCatalogSession = false
    private let api: APIClient
    private var context: ModelContext?
    private var cacheOwnerKey: String?
    private var activeTask: Task<Void, Never>?
    private var preferenceTask: Task<Void, Error>?
    private var cursor = SequenceCursor()
    private var identityGeneration = UUID()
    private var searchGeneration = UUID()
    private var nextHistoryBefore: String?
    private var loadedHistoryTurnIDs = Set<String>()
    private var favoritesRevision: UInt = 0
    private var historyRevision: UInt = 0

    init(api: APIClient) { self.api = api }

    func initializeCatalog(context: ModelContext) async {
        isCatalogSession = true
        self.context = context
        guard !isStarting else { return }
        isStarting = true
        defer { isStarting = false }
        do {
            try await api.bootstrap()
            cacheOwnerKey = await api.cacheOwnerKey()
            config = try await api.call("v1/config", authenticated: false)
            isReady = true
            initializationError = nil
        } catch { initializationError = friendly(error) }
    }

    func initialize(context: ModelContext, retry: Bool = false) async {
        if isCatalogSession { await initializeCatalog(context: context); return }
        guard !isStarting, !isRestoringConversation, !isApplyingFilters, !isDeleting, (!isReady || retry), !search.isStreaming else { return }
        self.context = context
        stop()
        let identity = identityGeneration
        let generation = searchGeneration
        isStarting = true
        initializationError = nil
        defer { if identity == identityGeneration { isStarting = false } }
        do {
            try await api.bootstrap()
            guard isCurrent(identity, generation) else { return }
            let owner = await api.cacheOwnerKey()
            guard isCurrent(identity, generation) else { return }
            cacheOwnerKey = owner
            if !isReady { favorites = (try? cachedFavorites()) ?? [] }
            let value: AppConfig = try await api.call("v1/config", authenticated: false)
            guard isCurrent(identity, generation) else { return }
            config = value
            let history: Items<ConversationSummary> = try await api.call("v1/conversations")
            guard isCurrent(identity, generation) else { return }
            conversations = history.items
            let pending = try await api.loadPending()
            guard isCurrent(identity, generation) else { return }
            let creation = try await api.loadPendingConversation()
            guard isCurrent(identity, generation) else { return }
            pendingRequest = pending
            pendingConversation = creation
            if let pending {
                try await restoreConversation(pending.conversationId, preservingPendingCreation: true, replacingSearch: false)
            } else if let creation {
                conversationID = nil
                search = SearchState()
                search.preferences = creation.preferences
                search.messages = [ChatMessage(isUser: true, text: creation.message)]
                resetHistoryPagination()
            } else if let latest = conversations.first { try await restoreConversation(latest.id, replacingSearch: false) }
            guard isCurrent(identity, generation) else { return }
            pendingRequest = pending
            if pending != nil || creation != nil { search.error = "Связь прервалась до подтверждения запроса. Проверьте сохранённый ответ — повторный подбор не запустится." }
            isReady = true
            requiresSessionReset = false
            needsConnectionRecovery = false
            isStarting = false
            await loadFavorites()
        } catch is CancellationError {
            // A replaced identity/search owns its own state.
        } catch {
            guard isCurrent(identity, generation) else { return }
            if config == nil, let publicConfig: AppConfig = try? await api.call("v1/config", authenticated: false) {
                guard isCurrent(identity, generation) else { return }
                config = publicConfig
            }
            guard isCurrent(identity, generation) else { return }
            initializationError = friendly(error)
            needsConnectionRecovery = true
            if !favorites.isEmpty {
                favoritesOffline = true
                favoriteError = "Нет связи с сервисом. Показаны ранее сохранённые квартиры; актуальное наличие не подтверждено."
                selectedTab = 1
                isReady = true
            }
        }
    }

    func retryInitialization() async {
        if let context { await initialize(context: context, retry: true) }
    }

    func send(_ text: String, selected: [String] = []) {
        let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard isReady, !isPreparingSearch, !isDeleting, !requiresSessionReset, !search.isStreaming, !message.isEmpty, message.unicodeScalars.count <= 2000 else { return }
        guard !search.isPending else {
            search.error = "Предыдущий подбор ещё обрабатывается. Проверьте сохранённый ответ немного позже."
            return
        }
        guard pendingRequest == nil, pendingConversation == nil else {
            search.error = "Сначала проверьте предыдущий запрос или начните новый подбор."
            return
        }
        stop()
        let identity = identityGeneration
        let generation = searchGeneration
        let preferences = search.preferences
        let existingConversation = conversationID
        let creation = existingConversation == nil ? PendingConversationRequest(preferences: preferences, message: message, selectedListingIds: selected, usesIdempotencyKey: config?.capabilities.contains("idempotent_conversation_create") == true) : nil
        pendingConversation = creation
        prepareStream()
        search.turnID = nil
        cursor = SequenceCursor()
        search.messages.append(ChatMessage(isUser: true, text: message))
        activeTask = Task {
            do {
                let request: PendingTurnRequest
                if let creation {
                    try await api.savePendingConversation(creation)
                    guard isCurrent(identity, generation) else {
                        try? await api.clearPendingConversation(ifMatching: creation.clientConversationId)
                        return
                    }
                    request = try await resolveCreation(creation, identity: identity, generation: generation)
                } else if let existingConversation {
                    request = PendingTurnRequest(conversationId: existingConversation, message: message, selectedListingIds: selected)
                    pendingRequest = request
                    try await api.savePending(request)
                    guard isCurrent(identity, generation) else {
                        try? await api.clearPending(ifMatching: request.clientTurnId)
                        return
                    }
                } else {
                    return
                }
                let events = try await api.stream(request: request)
                guard isCurrent(identity, generation) else { return }
                try await consume(events, identity: identity, generation: generation, request: request, messageAlreadyShown: true)
            } catch {
                finishWithError(error, identity: identity, generation: generation)
            }
        }
    }

    func resume() async {
        guard !search.isStreaming, !isPreparingSearch, !isDeleting, !requiresSessionReset else { return }
        let creation = pendingConversation
        let storedRequest = pendingRequest
        let conversationID = conversationID
        guard creation != nil || storedRequest != nil || (conversationID != nil && search.turnID != nil) else { return }
        if storedRequest == nil, let creation, !creation.usesIdempotencyKey {
            search.error = "Не удалось подтвердить создание подборки. Начните новый подбор, чтобы продолжить."
            return
        }
        stop()
        let identity = identityGeneration
        let generation = searchGeneration
        let turnID = search.turnID
        let after = cursor.sequence
        prepareStream()
        let task = Task { [self] in
            do {
                let events: AsyncThrowingStream<(Int?, ServerEvent), Error>
                let request: PendingTurnRequest?
                if let storedRequest {
                    request = storedRequest
                    events = try await api.stream(request: storedRequest)
                } else if let creation {
                    let resolved = try await resolveCreation(creation, identity: identity, generation: generation)
                    request = resolved
                    events = try await api.stream(request: resolved)
                }
                else if let turnID, let conversationID {
                    request = nil
                    events = try await api.replay(conversationID: conversationID, turnID: turnID, after: after)
                }
                else { return }
                guard isCurrent(identity, generation) else { return }
                let messageShown = request.map { search.messages.last(where: { $0.isUser })?.text == $0.message } ?? true
                try await consume(events, identity: identity, generation: generation, request: request, messageAlreadyShown: messageShown)
                guard isCurrent(identity, generation) else { return }
                if search.isPending { search.notice = "Подбор ещё обрабатывается. Можно проверить ответ немного позже." }
            } catch { finishWithError(error, identity: identity, generation: generation) }
        }
        activeTask = task
        await task.value
    }

    private func resolveCreation(_ creation: PendingConversationRequest, identity: UUID, generation: UUID) async throws -> PendingTurnRequest {
        // Creation is repeatable even if its first response was lost. The original
        // preferences and first-turn UUID remain immutable until the turn is durable.
        historyRevision &+= 1
        let created: ConversationSummary
        do { created = try await api.call("v1/conversations", method: "POST", body: creation.body()) }
        catch {
            if identity == identityGeneration { historyRevision &+= 1 }
            throw error
        }
        guard isCurrent(identity, generation) else { throw CancellationError() }
        historyRevision &+= 1
        conversations.removeAll { $0.id == created.id }
        conversations.insert(created, at: 0)
        conversationID = created.id
        let request = creation.turnRequest(conversationID: created.id)
        pendingRequest = request
        try await api.savePending(request)
        guard isCurrent(identity, generation) else {
            try? await api.clearPending(ifMatching: request.clientTurnId)
            throw CancellationError()
        }
        try await api.clearPendingConversation(ifMatching: creation.clientConversationId)
        guard isCurrent(identity, generation) else { throw CancellationError() }
        pendingConversation = nil
        return request
    }

    private func prepareStream() {
        search.isStreaming = true
        search.isPending = false
        search.error = nil
        search.notice = nil
        search.suggestions = []
    }

    private func consume(_ events: AsyncThrowingStream<(Int?, ServerEvent), Error>, identity: UUID, generation: UUID, request: PendingTurnRequest?, messageAlreadyShown: Bool) async throws {
        var messageShown = messageAlreadyShown
        for try await (sequence, event) in events {
            try Task.checkCancellation()
            guard isCurrent(identity, generation) else { return }
            if case .accepted(let id) = event {
                if let oldID = search.turnID, oldID != id {
                    cursor = SequenceCursor()
                    // A pending POST that never reached the server is a new turn.
                    messageShown = false
                }
                if !messageShown, let request {
                    search.messages.append(ChatMessage(isUser: true, text: request.message))
                    messageShown = true
                }
                search.turnID = id
                if let request {
                    if let creation = pendingConversation, creation.firstTurnId == request.clientTurnId {
                        try await api.clearPendingConversation(ifMatching: creation.clientConversationId)
                        guard isCurrent(identity, generation) else { return }
                        pendingConversation = nil
                    }
                    try await api.clearPending(ifMatching: request.clientTurnId)
                    guard isCurrent(identity, generation) else { return }
                    if pendingRequest?.clientTurnId == request.clientTurnId { pendingRequest = nil }
                }
            }
            guard cursor.accept(sequence) else {
                if case .done = event { search.apply(event) }
                continue
            }
            search.apply(event)
            if case .listings = event { selectedForComparison.formIntersection(search.apartments.map(\.id)) }
        }
        guard isCurrent(identity, generation) else { return }
        search.status = ""
        if search.isPending {
            search.isStreaming = false
            search.error = "Подбор ещё обрабатывается. Можно проверить сохранённый ответ немного позже."
        }
        else if search.isStreaming { throw StreamError.interrupted }
    }

    private func isCurrent(_ identity: UUID, _ searchID: UUID) -> Bool {
        identity == identityGeneration && searchID == searchGeneration
    }

    private func finishWithError(_ error: Error, identity: UUID, generation: UUID) {
        guard isCurrent(identity, generation) else { return }
        search.isStreaming = false
        search.status = ""
        if !(error is CancellationError) { search.error = friendly(error) }
    }

    func stop() {
        let wasStreaming = search.isStreaming
        searchGeneration = UUID()
        activeTask?.cancel()
        activeTask = nil
        preferenceTask?.cancel()
        preferenceTask = nil
        isRestoringConversation = false
        isApplyingFilters = false
        isLoadingOlderMessages = false
        search.isStreaming = false
        search.status = ""
        if wasStreaming, search.turnID != nil || pendingRequest != nil || pendingConversation != nil { search.error = "Подбор остановлен. Можно проверить сохранённый ответ." }
    }

    func newConversation() {
        guard !isDeleting else { return }
        stop()
        let oldPending = pendingRequest
        let oldCreation = pendingConversation
        pendingRequest = nil
        pendingConversation = nil
        if let oldPending { Task { try? await api.clearPending(ifMatching: oldPending.clientTurnId) } }
        if let oldCreation { Task { try? await api.clearPendingConversation(ifMatching: oldCreation.clientConversationId) } }
        let city = search.preferences.city
        search = SearchState()
        search.preferences.city = city
        conversationID = nil
        cursor = SequenceCursor()
        selectedForComparison = []
        resetHistoryPagination()
    }

    func applyFilters(_ preferences: Preferences) async throws {
        guard !isPreparingSearch, !isDeleting, !requiresSessionReset, !search.isStreaming, pendingRequest == nil, pendingConversation == nil else { throw CancellationError() }
        let identity = identityGeneration
        let generation = searchGeneration
        let id = conversationID
        isApplyingFilters = true
        defer {
            if isCurrent(identity, generation) { isApplyingFilters = false; preferenceTask = nil }
        }
        let task = Task { [self] in
            if let id {
                let _: Preferences = try await api.call("v1/conversations/\(id)/preferences", method: "PUT", body: APIJSON.encoder().encode(preferences))
            }
            try Task.checkCancellation()
            guard isCurrent(identity, generation), !isDeleting else { throw CancellationError() }
            search.preferences = preferences
            isApplyingFilters = false
            preferenceTask = nil
            send("Покажи квартиры по моим условиям")
        }
        preferenceTask = task
        do { try await task.value }
        catch {
            guard isCurrent(identity, generation) else { throw CancellationError() }
            throw error
        }
    }

    func restoreConversation(_ id: String, preservingPendingCreation: Bool = false, replacingSearch: Bool = true) async throws {
        guard !isDeleting else { throw CancellationError() }
        if replacingSearch { stop() }
        let identity = identityGeneration
        let generation = searchGeneration
        isRestoringConversation = true
        defer { if isCurrent(identity, generation) { isRestoringConversation = false } }
        if !preservingPendingCreation, let creation = pendingConversation {
            do { try await api.clearPendingConversation(ifMatching: creation.clientConversationId) }
            catch {
                guard isCurrent(identity, generation) else { throw CancellationError() }
                throw error
            }
            guard isCurrent(identity, generation) else { throw CancellationError() }
            pendingConversation = nil
        }
        if let pending = pendingRequest, pending.conversationId != id {
            do { try await api.clearPending(ifMatching: pending.clientTurnId) }
            catch {
                guard isCurrent(identity, generation) else { throw CancellationError() }
                throw error
            }
            guard isCurrent(identity, generation) else { throw CancellationError() }
            pendingRequest = nil
        }
        let value: ConversationHistory
        do { value = try await api.call("v1/conversations/\(id)") }
        catch {
            guard isCurrent(identity, generation) else { throw CancellationError() }
            throw error
        }
        guard isCurrent(identity, generation) else { throw CancellationError() }
        var state = SearchState()
        for turn in value.turns {
            state.messages.append(ChatMessage(isUser: true, text: turn.message))
            for event in turn.events { state.apply(try event.streamEvent()) }
        }
        state.preferences = value.preferences
        state.isStreaming = false
        cursor = SequenceCursor()
        if let latest = value.turns.last {
            state.turnID = latest.id
            cursor = SequenceCursor(sequence: latest.events.map(\.sequence).max() ?? 0)
            if latest.status != "complete" { state.error = "Подбор ещё не завершён. Можно проверить сохранённый ответ." }
        }
        state.status = ""
        state.notice = value.turns.isEmpty ? nil : "Предыдущая подборка сохранена. Перед выбором уточните актуальное наличие."
        search = state
        conversationID = id
        selectedForComparison = []
        loadedHistoryTurnIDs = Set(value.turns.map(\.id))
        updateHistoryPagination(value)
    }

    private func resetHistoryPagination() {
        hasOlderMessages = false
        isLoadingOlderMessages = false
        historyLoadError = nil
        nextHistoryBefore = nil
        loadedHistoryTurnIDs = []
    }

    private func updateHistoryPagination(_ page: ConversationHistory) {
        nextHistoryBefore = page.nextBefore
        hasOlderMessages = page.hasMore && page.nextBefore != nil
        historyLoadError = page.hasMore && page.nextBefore == nil ? "Не удалось прочитать продолжение истории. Откройте диалог снова." : nil
    }

    func loadOlderMessages() async {
        guard !isLoadingOlderMessages, !isPreparingSearch, !isDeleting, !requiresSessionReset, !search.isStreaming,
              hasOlderMessages, let before = nextHistoryBefore, let id = conversationID else { return }
        let identity = identityGeneration
        let generation = searchGeneration
        isLoadingOlderMessages = true
        historyLoadError = nil
        defer { if isCurrent(identity, generation) { isLoadingOlderMessages = false } }
        do {
            let page: ConversationHistory = try await api.call("v1/conversations/\(id)?before=\(before)")
            guard isCurrent(identity, generation), conversationID == id else { return }
            if page.hasMore, page.nextBefore == nil || page.nextBefore == before { throw APIError.invalidResponse }
            var seen = loadedHistoryTurnIDs
            var earlier: [ChatMessage] = []
            for turn in page.turns where seen.insert(turn.id).inserted {
                earlier.append(ChatMessage(isUser: true, text: turn.message))
                for event in turn.events.sorted(by: { $0.sequence < $1.sequence }) where event.kind == "message" {
                    if case .message(let text, let citations) = try event.streamEvent() {
                        earlier.append(ChatMessage(isUser: false, text: text, citations: citations))
                    }
                }
            }
            // Older pages contain historical listings/preferences too. Only messages
            // are prepended: today's cards and the newest turn's replay cursor stay intact.
            search.messages.insert(contentsOf: earlier, at: 0)
            loadedHistoryTurnIDs = seen
            updateHistoryPagination(page)
        } catch {
            guard isCurrent(identity, generation), !(error is CancellationError) else { return }
            historyLoadError = friendly(error)
        }
    }

    func refreshHistory() async {
        let identity = identityGeneration
        let revision = historyRevision
        let generation = searchGeneration
        do {
            let result: Items<ConversationSummary> = try await api.call("v1/conversations")
            guard identity == identityGeneration, revision == historyRevision else { return }
            conversations = result.items
        } catch {
            guard isCurrent(identity, generation), revision == historyRevision, !(error is CancellationError) else { return }
            search.error = friendly(error)
        }
    }

    func deleteConversation(_ id: String) async throws {
        guard !isDeleting, !isStarting else { throw CancellationError() }
        let identity = identityGeneration
        historyRevision &+= 1
        if conversationID == id { stop() }
        let generation = searchGeneration
        do { try await api.perform("v1/conversations/\(id)", method: "DELETE") }
        catch {
            guard identity == identityGeneration else { throw CancellationError() }
            historyRevision &+= 1
            throw error
        }
        guard identity == identityGeneration else { throw CancellationError() }
        historyRevision &+= 1
        conversations.removeAll { $0.id == id }
        if conversationID == id, generation == searchGeneration { newConversation() }
    }

    func loadFavorites() async {
        let identity = identityGeneration
        let revision = favoritesRevision
        do {
            if config == nil {
                let value: AppConfig = try await api.call("v1/config", authenticated: false)
                guard identity == identityGeneration else { return }
                config = value
            }
            let result: Items<Apartment> = try await api.call("v1/favorites")
            guard identity == identityGeneration, revision == favoritesRevision else { return }
            favorites = result.items
            favoritesOffline = false
            favoriteError = nil
            do { try saveCache() }
            catch { favoriteError = "Избранное обновлено с сервера, но не удалось сохранить его на устройстве." }
        } catch {
            guard identity == identityGeneration, revision == favoritesRevision, !(error is CancellationError) else { return }
            _ = friendly(error)
            favoritesOffline = true
            favoriteError = "Не удалось обновить избранное. Сохранённые предложения могут измениться."
        }
    }

    func toggleFavorite(_ apartment: Apartment) async {
        guard !isDeleting, !requiresSessionReset, !isMutatingFavorite.contains(apartment.id) else { return }
        let identity = identityGeneration
        favoritesRevision &+= 1
        isMutatingFavorite.insert(apartment.id)
        defer { if identity == identityGeneration { favoritesRevision &+= 1; isMutatingFavorite.remove(apartment.id) } }
        do {
            let exists = favorites.contains { $0.id == apartment.id }
            try await api.perform("v1/favorites/\(apartment.id)", method: exists ? "DELETE" : "PUT")
            guard identity == identityGeneration else { return }
            if exists { favorites.removeAll { $0.id == apartment.id } }
            else { favorites.insert(apartment, at: 0) }
            do { try saveCache(); favoriteError = nil }
            catch { favoriteError = "Избранное изменено на сервере, но не удалось обновить сохранённую копию на устройстве." }
        } catch {
            guard identity == identityGeneration, !(error is CancellationError) else { return }
            favoriteError = friendly(error)
        }
    }

    private func cachedFavorites() throws -> [Apartment] {
        guard let context, let cacheOwnerKey else { return [] }
        let owner = cacheOwnerKey
        let cached = try context.fetch(FetchDescriptor<CachedFavorite>(predicate: #Predicate { $0.ownerKey == owner }))
        return cached.sorted { $0.savedAt > $1.savedAt }.compactMap { try? APIJSON.decoder().decode(Apartment.self, from: $0.payload) }
    }

    private func saveCache() throws {
        guard let context, let cacheOwnerKey else { return }
        let owner = cacheOwnerKey
        for item in try context.fetch(FetchDescriptor<CachedFavorite>(predicate: #Predicate { $0.ownerKey == owner })) { context.delete(item) }
        for apartment in favorites { context.insert(try CachedFavorite(apartment: apartment, ownerKey: owner)) }
        try context.save()
    }

    func verify(_ apartment: Apartment) async throws -> Verification {
        let identity = identityGeneration
        let generation = searchGeneration
        favoritesRevision &+= 1
        defer { if identity == identityGeneration { favoritesRevision &+= 1 } }
        let result: Verification
        do { result = try await api.call("v1/apartments/\(apartment.id)/verify", method: "POST") }
        catch {
            guard identity == identityGeneration else { throw CancellationError() }
            throw error
        }
        guard identity == identityGeneration else { throw CancellationError() }
        if let fresh = result.listing {
            if let index = favorites.firstIndex(where: { $0.id == fresh.id }) { favorites[index] = fresh }
            if generation == searchGeneration {
                if fresh.status != "available" { search.apartments.removeAll { $0.id == fresh.id } }
                else if let index = search.apartments.firstIndex(where: { $0.id == fresh.id }) { search.apartments[index] = fresh }
            }
            try saveCache()
        }
        return result
    }

    func explain(_ apartment: Apartment) {
        selectedTab = 0
        send("Почему мне подходит эта квартира?", selected: [apartment.id])
    }

    func compare() {
        selectedTab = 0
        send("Сравни выбранные квартиры", selected: Array(selectedForComparison).sorted())
    }

    private func clearLocalIdentity() throws {
        favorites = []
        search = SearchState()
        conversations = []
        conversationID = nil
        pendingRequest = nil
        pendingConversation = nil
        selectedForComparison = []
        isMutatingFavorite = []
        config = nil
        isReady = false
        isStarting = false
        favoritesOffline = false
        favoriteError = nil
        needsConnectionRecovery = false
        cursor = SequenceCursor()
        resetHistoryPagination()
        if let context {
            try context.delete(model: CachedFavorite.self)
            try context.save()
        }
        cacheOwnerKey = nil
    }

    func generateRecoveryCode() async throws -> String {
        guard supportsAccountRecovery, !isDeleting, !requiresSessionReset else { throw APIError.invalidConfiguration }
        let identity = identityGeneration
        do {
            let code = try await api.generateRecoveryCode()
            guard identity == identityGeneration else { throw CancellationError() }
            return code
        } catch {
            guard identity == identityGeneration else { throw CancellationError() }
            throw error
        }
    }

    func recoverAccount(code: String) async throws {
        guard canOfferAccountRecovery, !isDeleting else { throw APIError.invalidConfiguration }
        let value = code.trimmingCharacters(in: .whitespacesAndNewlines)
        guard value.count == 43, value.unicodeScalars.allSatisfy({ CharacterSet(charactersIn: "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_").contains($0) }) else {
            throw APIError.http(401, "invalid_recovery_code")
        }
        isDeleting = true
        stop()
        do {
            try await api.recoverAccount(code: value)
        } catch {
            isDeleting = false
            if !isReady {
                initializationError = friendly(error)
                needsConnectionRecovery = true
            }
            throw error
        }
        identityGeneration = UUID()
        stop()
        do { try clearLocalIdentity() }
        catch {
            requiresSessionReset = false
            needsConnectionRecovery = true
            initializationError = APIError.localCleanupAfterRecovery.localizedDescription
            isDeleting = false
            throw APIError.localCleanupAfterRecovery
        }
        requiresSessionReset = false
        isDeleting = false
        if let context { await initialize(context: context) }
    }

    func startNewSession() async {
        guard !isDeleting else { return }
        isDeleting = true
        identityGeneration = UUID()
        stop()
        do {
            try await api.resetSession()
            try clearLocalIdentity()
            requiresSessionReset = false
            isDeleting = false
            if let context { await initialize(context: context) }
        } catch {
            try? clearLocalIdentity()
            requiresSessionReset = true
            initializationError = friendly(error)
            isDeleting = false
        }
    }

    func deleteAccount() async throws {
        guard !isDeleting else { return }
        isDeleting = true
        identityGeneration = UUID()
        stop()
        defer { isDeleting = false }
        do {
            try await api.deleteAccount()
        } catch APIError.localCleanupAfterDeletion {
            try? clearLocalIdentity()
            requiresSessionReset = true
            initializationError = APIError.localCleanupAfterDeletion.localizedDescription
            throw APIError.localCleanupAfterDeletion
        } catch {
            _ = friendly(error)
            throw error
        }
        do { try clearLocalIdentity() }
        catch {
            requiresSessionReset = true
            initializationError = APIError.localCleanupAfterDeletion.localizedDescription
            throw APIError.localCleanupAfterDeletion
        }
        requiresSessionReset = false
        isDeleting = false
        if let context { await initialize(context: context) }
    }

    func friendly(_ error: Error) -> String {
        if error is StreamError { return "Связь прервалась. Полученные варианты сохранены. Проверьте ответ или отправьте запрос снова." }
        if let error = error as? APIError {
            if error.requiresSessionReset { requiresSessionReset = true }
            return error.localizedDescription
        }
        if let error = error as? VaultError { return error.localizedDescription }
        return "Не удалось связаться с сервисом. Проверьте интернет и повторите попытку."
    }
}
