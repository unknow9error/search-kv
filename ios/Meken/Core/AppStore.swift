import Foundation
import Observation
import SwiftData
import MekenCore

@Model
final class CachedFavorite {
    @Attribute(.unique) var id: String
    var payload: Data
    var savedAt: Date
    init(apartment: Apartment) throws {
        id = apartment.id
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
    var selectedTab = 0
    var selectedForComparison = Set<String>()
    var isMutatingFavorite = Set<String>()
    private let api: APIClient
    private var context: ModelContext?
    private var activeTask: Task<Void, Never>?
    private var lastSequence = 0

    init(api: APIClient) { self.api = api }

    func initialize(context: ModelContext) async {
        guard !isStarting, !isReady else { return }
        self.context = context
        isStarting = true
        initializationError = nil
        defer { isStarting = false }
        if let cached = try? context.fetch(FetchDescriptor<CachedFavorite>()) {
            favorites = cached.compactMap { try? APIJSON.decoder().decode(Apartment.self, from: $0.payload) }
        }
        do {
            try await api.bootstrap()
            config = try await api.call("v1/config", authenticated: false)
            let history: Items<ConversationSummary> = try await api.call("v1/conversations")
            conversations = history.items
            if let latest = conversations.first { try await restoreConversation(latest.id) }
            isReady = true
            await loadFavorites()
        } catch {
            initializationError = friendly(error)
            if !favorites.isEmpty {
                favoritesOffline = true
                favoriteError = "Нет связи с сервисом. Показаны ранее сохранённые квартиры; актуальное наличие не подтверждено."
                selectedTab = 1
                isReady = true
            }
        }
    }

    func send(_ text: String, selected: [String] = []) {
        let message = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard isReady, !search.isStreaming, !message.isEmpty, message.unicodeScalars.count <= 2000 else { return }
        search.isStreaming = true
        search.isPending = false
        search.error = nil
        search.notice = nil
        search.suggestions = []
        search.turnID = nil
        lastSequence = 0
        search.messages.append(ChatMessage(isUser: true, text: message))
        activeTask = Task {
            do {
                if conversationID == nil {
                    struct Create: Encodable { let preferences: Preferences }
                    let created: ConversationSummary = try await api.call("v1/conversations", method: "POST", body: APIJSON.encoder().encode(Create(preferences: search.preferences)))
                    conversationID = created.id
                }
                guard let conversationID else { return }
                let events = try await api.stream(conversationID: conversationID, message: message, selected: selected, clientTurnID: UUID())
                for try await (sequence, event) in events {
                    try Task.checkCancellation()
                    if let sequence { lastSequence = sequence }
                    search.apply(event)
                }
                if search.isStreaming { throw StreamError.interrupted }
            } catch is CancellationError {
                search.isStreaming = false
                search.status = ""
            } catch {
                search.isStreaming = false
                search.status = ""
                search.error = friendly(error)
            }
        }
    }

    func resume() async {
        guard let conversationID, let turnID = search.turnID, !search.isStreaming else { return }
        search.error = nil
        search.isStreaming = true
        defer { search.isStreaming = false }
        do {
            let stream = try await api.replay(conversationID: conversationID, turnID: turnID, after: lastSequence)
            for try await (sequence, event) in stream {
                if let sequence { lastSequence = sequence }
                search.apply(event)
            }
            if search.isPending { search.notice = "Подбор ещё обрабатывается. Можно проверить ответ немного позже." }
        } catch { search.error = friendly(error) }
    }

    func stop() { activeTask?.cancel(); activeTask = nil; search.isStreaming = false; search.status = "" }

    func newConversation() {
        stop()
        let city = search.preferences.city
        search = SearchState()
        search.preferences.city = city
        conversationID = nil
        selectedForComparison = []
    }

    func applyFilters(_ preferences: Preferences) async throws {
        if let conversationID {
            let _: Preferences = try await api.call("v1/conversations/\(conversationID)/preferences", method: "PUT", body: APIJSON.encoder().encode(preferences))
        }
        search.preferences = preferences
        send("Покажи квартиры по моим условиям")
    }

    func restoreConversation(_ id: String) async throws {
        stop()
        let value: ConversationHistory = try await api.call("v1/conversations/\(id)")
        var state = SearchState()
        for turn in value.turns {
            state.messages.append(ChatMessage(isUser: true, text: turn.message))
            for event in turn.events { state.apply(try event.streamEvent()) }
        }
        state.preferences = value.preferences
        state.isStreaming = false
        if let latest = value.turns.last {
            state.turnID = latest.id
            lastSequence = latest.events.map(\.sequence).max() ?? 0
            if latest.status != "complete" {
                state.error = "Подбор ещё не завершён. Можно проверить сохранённый ответ."
            }
        }
        state.status = ""
        state.notice = value.turns.isEmpty ? nil : "Предыдущая подборка сохранена. Перед выбором уточните актуальное наличие."
        search = state
        conversationID = id
    }

    func refreshHistory() async {
        do {
            let result: Items<ConversationSummary> = try await api.call("v1/conversations")
            conversations = result.items
        }
        catch { search.error = friendly(error) }
    }

    func loadFavorites() async {
        do {
            if config == nil { config = try await api.call("v1/config", authenticated: false) }
            let result: Items<Apartment> = try await api.call("v1/favorites")
            favorites = result.items
            favoritesOffline = false
            favoriteError = nil
            try saveCache()
        } catch {
            favoritesOffline = true
            favoriteError = "Не удалось обновить избранное. Сохранённые предложения могут измениться."
            if let cached = try? context?.fetch(FetchDescriptor<CachedFavorite>()) {
                favorites = cached.compactMap { try? APIJSON.decoder().decode(Apartment.self, from: $0.payload) }
            }
        }
    }

    func toggleFavorite(_ apartment: Apartment) async {
        guard !isMutatingFavorite.contains(apartment.id) else { return }
        isMutatingFavorite.insert(apartment.id)
        defer { isMutatingFavorite.remove(apartment.id) }
        do {
            let exists = favorites.contains { $0.id == apartment.id }
            try await api.perform("v1/favorites/\(apartment.id)", method: exists ? "DELETE" : "PUT")
            if exists { favorites.removeAll { $0.id == apartment.id } }
            else { favorites.insert(apartment, at: 0) }
            try saveCache()
            favoriteError = nil
        } catch { favoriteError = friendly(error) }
    }

    private func saveCache() throws {
        guard let context else { return }
        try context.delete(model: CachedFavorite.self)
        for apartment in favorites { context.insert(try CachedFavorite(apartment: apartment)) }
        try context.save()
    }

    func verify(_ apartment: Apartment) async throws -> Verification {
        let result: Verification = try await api.call("v1/apartments/\(apartment.id)/verify", method: "POST")
        if let fresh = result.listing {
            if let index = favorites.firstIndex(where: { $0.id == fresh.id }) { favorites[index] = fresh }
            if fresh.status != "available" { search.apartments.removeAll { $0.id == fresh.id } }
            else if let index = search.apartments.firstIndex(where: { $0.id == fresh.id }) { search.apartments[index] = fresh }
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

    func deleteAccount() async throws {
        stop()
        try await api.deleteAccount()
        favorites = []
        try saveCache()
        search = SearchState()
        conversations = []
        conversationID = nil
        isReady = false
        if let context { await initialize(context: context) }
    }

    func friendly(_ error: Error) -> String {
        if error is StreamError { return "Связь прервалась. Полученные варианты сохранены. Проверьте ответ или отправьте запрос снова." }
        if let error = error as? APIError { return error.localizedDescription }
        if let error = error as? VaultError { return error.localizedDescription }
        return "Не удалось связаться с сервисом. Проверьте интернет и повторите попытку."
    }
}
