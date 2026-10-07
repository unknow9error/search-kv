import SwiftUI
import Observation
import MekenCore

enum CatalogPage: String, CaseIterable { case search, filters, results, map, conversation, project, layout, comparison, favorites, history, profile, help, sources, report }
struct PendingProjectMessage: Codable, Sendable {
    var clientConversationId: String; var conversationId: String?; let clientTurnId: String; let message: String; let criteria: ProjectCriteria
}
struct ProjectOfflineCache: Codable { let config: AppConfig; let favorites: [CatalogProject] }

@Observable @MainActor
final class CatalogStore {
    let api: APIClient
    var config: AppConfig?
    var criteria = ProjectCriteria(city: UserDefaults.standard.string(forKey: "catalog-city"))
    var facets: ProjectFacets?
    var results: [CatalogProject] = []; var total = 0; var cursor: String?; var sort = "price_asc"
    var mapped: [CatalogProject] = []; var mapBounds: ProjectBounds?; var unknownCoordinates = 0
    var favorites: [CatalogProject] = []; var selected: [String] = []; var favoriteBusy = Set<String>()
    var comparison: ProjectComparison?; var project: CatalogProject?; var layout: ProjectLayout?
    var conversations: [ProjectConversation] = []; var historyTurns: [ProjectHistoryTurn] = []
    var conversationId: String?; var conversationTitle = "Разговор"; var historyCursor: String?; var suggestions: [String] = []
    var sources: [CatalogSource] = []; var error: String?; var busy = false; var ready = false; var offline = false
    var selectedTab = 0; private var paths: [[CatalogPage]] = [[.search], [.favorites], [.profile]]
    private var facetRevision = 0; var facetCity: String?; private var searchRevision = 0; private var scopeRevision = 0
    private var ownerKey: String?; private var cache: TokenVault?; private var pendingVault: TokenVault?; var pending: PendingProjectMessage?
    var page: CatalogPage { paths[selectedTab].last ?? .search }
    var tabsVisible: Bool { [.search, .results, .map, .favorites, .profile].contains(page) }
    var supported: Bool { config?.capabilities.contains("project_catalog") == true }
    private var canRequestCatalog: Bool { supported && !offline }
    var cities: [String] { config?.cities ?? [] }
    init(api: APIClient) { self.api = api }
    func push(_ page: CatalogPage) { if page == .filters { Task { await loadFacets() } }; paths[selectedTab].append(page); error = nil }
    func back() { if paths[selectedTab].count > 1 { paths[selectedTab].removeLast() }; error = nil }
    func tab(_ value: Int) { selectedTab = value; error = nil }
    func replace(_ page: CatalogPage) { paths[selectedTab] = [page]; error = nil }
    func clearScope() { scopeRevision += 1; searchRevision += 1; favorites = []; results = []; mapped = []; selected = []; conversations = []; historyTurns = []; conversationId = nil; project = nil; layout = nil; comparison = nil; pending = nil; cache = nil; pendingVault = nil; ownerKey = nil; ready = false; paths = [[.search], [.favorites], [.profile]]; selectedTab = 0 }
    func synchronizeIdentity() async {
        if await api.cacheOwnerKey() != ownerKey { clearScope(); await initialize() }
    }
    func erasePrivateCache() throws { try cache?.clear(); try pendingVault?.clear() }
    func friendly(_ failure: Error) -> String { (failure as? APIError)?.errorDescription ?? "Не удалось загрузить сведения. Проверьте подключение и попробуйте снова." }
    func initialize() async {
        let scope = scopeRevision; busy = true; error = nil
        defer { if scope == scopeRevision { busy = false } }
        do {
            try await api.bootstrap()
            guard scope == scopeRevision else { return }
            if let owner = await api.cacheOwnerKey() {
                ownerKey = owner
                cache = TokenVault(service: "kz.meken.catalog." + owner, account: "favorites")
                pendingVault = TokenVault(service: "kz.meken.catalog." + owner, account: "pending")
                if let saved = try cache?.loadValue(ProjectOfflineCache.self) { config = saved.config; favorites = saved.favorites }
                pending = try pendingVault?.loadValue(PendingProjectMessage.self)
            }
            let value: AppConfig = try await api.call("v1/config", authenticated: false)
            guard scope == scopeRevision else { return }
            config = value; ready = true; offline = false
            if criteria.city == nil || !value.cities.contains(criteria.city ?? "") { criteria.city = value.cities.contains("Астана") ? "Астана" : value.cities.first }
            if supported { await loadFacets(); await loadFavorites(); await loadHistory() }
        } catch is CancellationError {} catch {
            guard scope == scopeRevision else { return }
            offline = true; ready = config != nil; self.error = friendly(error)
        }
    }
    func loadFacets(city requestedCity: String? = nil) async {
        guard canRequestCatalog else { return }
        facetRevision += 1; let revision = facetRevision
        let scope = scopeRevision; let city = requestedCity ?? criteria.city
        do {
            let query = city.map { "?city=" + $0.addingPercentEncoding(withAllowedCharacters: .urlQueryAllowed)! } ?? ""
            let value: ProjectFacets = try await api.call("v1/projects/facets" + query)
            if scope == scopeRevision, revision == facetRevision { facets = value; facetCity = city }
        } catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func chooseCity(_ city: String) { criteria.city = city; criteria.districts = []; UserDefaults.standard.set(city, forKey: "catalog-city"); Task { await loadFacets() } }
    func search(more: Bool = false) async {
        guard canRequestCatalog else { return }
        searchRevision += 1; let revision = searchRevision; let scope = scopeRevision; let requested = criteria
        busy = true; error = nil
        defer { if revision == searchRevision, scope == scopeRevision { busy = false } }
        do {
            let body = ProjectSearchBody(criteria: requested, cursor: more ? cursor : nil, sort: sort)
            let value: ProjectPage = try await api.call("v1/projects/search", method: "POST", body: APIJSON.encoder().encode(body))
            guard revision == searchRevision, scope == scopeRevision, requested == criteria else { return }
            if more { let existing = Set(results.map(\.id)); results += value.items.filter { !existing.contains($0.id) } } else { results = value.items }
            total = value.total; cursor = value.nextCursor; unknownCoordinates = value.unknownCoordinatesCount
            selected = selected.filter { Set(results.map(\.id) + favorites.map(\.id)).contains($0) }
            if page == .filters { back() }
            if page != .results { push(.results) }
        } catch is CancellationError {} catch { if revision == searchRevision, scope == scopeRevision { self.error = friendly(error) } }
    }
    func loadMap() async {
        guard canRequestCatalog else { return }; let scope = scopeRevision; var requested = criteria; requested.bounds = mapBounds
        do {
            let value: ProjectPage = try await api.call("v1/projects/map", method: "POST", body: APIJSON.encoder().encode(ProjectSearchBody(criteria: requested, sort: sort)))
            guard scope == scopeRevision else { return }; mapped = value.items; unknownCoordinates = value.unknownCoordinatesCount
        } catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func openProject(_ value: CatalogProject) {
        project = value; push(.project)
        let scope = scopeRevision
        Task {
            guard canRequestCatalog else { return }
            do { let item: CatalogProject = try await api.call("v1/projects/" + value.id); if scope == scopeRevision, project?.id == value.id { project = item } }
            catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
        }
    }
    func openLayout(_ value: ProjectLayout) { layout = value; push(.layout) }
    func loadFavorites() async {
        guard canRequestCatalog else { return }
        let scope = scopeRevision
        do { let value: Items<CatalogProject> = try await api.call("v1/projects/favorites"); guard scope == scopeRevision else { return }; favorites = value.items; if let config { try cache?.saveValue(ProjectOfflineCache(config: config, favorites: favorites)) } }
        catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func favorite(_ value: CatalogProject) async {
        guard canRequestCatalog, !favoriteBusy.contains(value.id) else { return }; let scope = scopeRevision; favoriteBusy.insert(value.id)
        defer { if scope == scopeRevision { favoriteBusy.remove(value.id) } }
        do {
            let removing = favorites.contains { $0.id == value.id }
            try await api.perform("v1/projects/favorites/" + value.id, method: removing ? "DELETE" : "PUT")
            guard scope == scopeRevision else { return }
            if removing { favorites.removeAll { $0.id == value.id } } else { favorites.insert(value, at: 0) }
            if let config { try cache?.saveValue(ProjectOfflineCache(config: config, favorites: favorites)) }
        } catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func select(_ id: String) { if selected.contains(id) { selected.removeAll { $0 == id } } else if selected.count < 3 { selected.append(id) } }
    func compare() async {
        guard canRequestCatalog, selected.count >= 2 else { return }; let scope = scopeRevision; busy = true; error = nil
        defer { if scope == scopeRevision { busy = false } }
        struct Body: Encodable { let projectIds: [String] }
        do { let value: ProjectComparison = try await api.call("v1/projects/compare", method: "POST", body: APIJSON.encoder().encode(Body(projectIds: selected))); if scope == scopeRevision {
                var projects: [CatalogProject] = []
                for var summary in value.projects {
                    let detail: CatalogProject = try await api.call("v1/projects/" + summary.id)
                    if let version = summary.version, detail.version == version { summary.buildings = detail.buildings }
                    projects.append(summary)
                }
                guard scope == scopeRevision else { return }
                comparison = ProjectComparison(projects: projects, rows: value.rows); push(.comparison)
            } }
        catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func loadHistory() async {
        guard canRequestCatalog else { return }
        let scope = scopeRevision
        do { let value: Items<ProjectConversation> = try await api.call("v1/projects/conversations"); if scope == scopeRevision { conversations = value.items } }
        catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func restore(_ value: ProjectConversation, more: Bool = false) async {
        guard canRequestCatalog else { return }
        let scope = scopeRevision; busy = true
        defer { if scope == scopeRevision { busy = false } }
        do {
            let suffix = more ? historyCursor.map { "?before=" + $0 } ?? "" : ""
            let history: ProjectHistory = try await api.call("v1/projects/conversations/" + value.id + suffix)
            guard scope == scopeRevision else { return }
            if more { historyTurns = history.turns + historyTurns } else {
                historyTurns = history.turns; criteria = history.criteria; conversationId = history.id; conversationTitle = history.title
                if let reply = history.turns.last?.response { results = reply.results?.items ?? []; suggestions = reply.suggestions }
                if page != .conversation { push(.conversation) }
            }
            historyCursor = history.nextBefore
        } catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func deleteConversation(_ id: String) async {
        guard canRequestCatalog else { return }
        do { try await api.perform("v1/projects/conversations/" + id, method: "DELETE"); conversations.removeAll { $0.id == id }; if conversationId == id { newSearch() } }
        catch { self.error = friendly(error) }
    }
    func newSearch() { conversationId = nil; historyTurns = []; suggestions = []; pending = nil; try? pendingVault?.clear(); criteria = ProjectCriteria(city: criteria.city); results = []; mapped = []; selected = []; paths[0] = [.search]; paths[2] = [.profile]; selectedTab = 0; error = nil }
    func send(_ message: String, retry: Bool = false) async {
        guard !busy, supported, !offline else { return }
        guard retry || pending == nil else { error = "Сначала повторите отправку сохранённого сообщения."; return }
        let text = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard retry || (!text.isEmpty && text.unicodeScalars.count <= 2000) else { return }
        let scope = scopeRevision; busy = true; error = nil
        defer { if scope == scopeRevision { busy = false } }
        do {
            var request = retry ? pending! : PendingProjectMessage(clientConversationId: UUID().uuidString, conversationId: conversationId, clientTurnId: UUID().uuidString, message: text, criteria: criteria)
            try pendingVault?.saveValue(request); pending = request
            if request.conversationId == nil {
                struct Create: Encodable { let criteria: ProjectCriteria; let clientConversationId: String }
                let created: ProjectConversation = try await api.call("v1/projects/conversations", method: "POST", body: APIJSON.encoder().encode(Create(criteria: request.criteria, clientConversationId: request.clientConversationId)))
                guard scope == scopeRevision else { return }; request.conversationId = created.id; pending = request; conversationId = created.id; try pendingVault?.saveValue(request)
            }
            struct Body: Encodable { let clientTurnId: String; let message: String; let criteria: ProjectCriteria }
            let reply: ProjectTurn = try await api.call("v1/projects/conversations/" + request.conversationId! + "/turns", method: "POST", body: APIJSON.encoder().encode(Body(clientTurnId: request.clientTurnId, message: request.message, criteria: request.criteria)))
            guard scope == scopeRevision else { return }
            pending = nil; try pendingVault?.clear(); criteria = reply.criteria; results = reply.results?.items ?? results; suggestions = reply.suggestions
            await loadHistory()
            if let conversation = conversations.first(where: { $0.id == reply.conversationId }) { await restore(conversation) }
        } catch is CancellationError {} catch { if scope == scopeRevision { self.error = friendly(error) } }
    }
    func loadSources() async {
        guard canRequestCatalog else { return }
        do { let value: Items<CatalogSource> = try await api.call("v1/catalog/sources"); sources = value.items }
        catch { self.error = friendly(error) }
    }
    func report(id: String, projectId: String, category: String, message: String) async -> Bool {
        guard canRequestCatalog else { return false }
        struct Body: Encodable { let clientReportId, projectId, category, message: String }
        struct Receipt: Decodable, Sendable { let status: String }
        do {
            let receipt: Receipt = try await api.call("v1/data-reports", method: "POST", body: APIJSON.encoder().encode(Body(clientReportId: id, projectId: projectId, category: category, message: message)))
            return receipt.status == "received"
        } catch { self.error = friendly(error); return false }
    }
}
