#!/usr/bin/env python3
"""Run isolated macOS SwiftData regressions against the current iOS AppStore.

The API is mocked; no production network, Keychain, or persistent app cache is used.
Requires macOS and Xcode with Swift 6. This supplements iOS builds and device tests.
"""
from pathlib import Path
import shutil
import os
import subprocess
import tempfile
root = Path(tempfile.mkdtemp(prefix='meken-ios-reliability-'))
(root/'Sources/MekenCore').mkdir(parents=True, exist_ok=True)
(root/'Sources/MobileStore').mkdir(parents=True, exist_ok=True)
(root/'Tests/MobileStoreTests').mkdir(parents=True, exist_ok=True)
source = Path(__file__).resolve().parents[1]
for file in (source/'ios/MekenCore/Sources/MekenCore').glob('*.swift'):
    shutil.copy(file, root/'Sources/MekenCore'/file.name)
shutil.copy(source/'ios/Meken/Core/AppStore.swift', root/'Sources/MobileStore/AppStore.swift')
(root/'Package.swift').write_text('''// swift-tools-version: 6.0
import PackageDescription
let package = Package(name: "MekenReliabilityHarness", platforms: [.macOS(.v14)], products: [], targets: [
.target(name: "MekenCore"), .target(name: "MobileStore", dependencies: ["MekenCore"]),
.testTarget(name: "MobileStoreTests", dependencies: ["MobileStore", "MekenCore"])
])
''')
(root/'Sources/MobileStore/APIClient.swift').write_text(r'''import Foundation
import MekenCore

enum APIError: Error, LocalizedError {
    case http(Int,String), noSession, invalidResponse, invalidConfiguration
    case localCleanupAfterDeletion, localCleanupAfterRecovery
    var requiresSessionReset: Bool {
        if case .http(401, "session_expired") = self { return true }
        return false
    }
    var errorDescription: String? { "stub error" }
}
struct VaultError: Error, LocalizedError { var errorDescription: String? { "vault error" } }
struct ReplayCall: Sendable { let conversationID: String; let turnID: String; let after: Int }
actor APIClient {
    private var pending: PendingTurnRequest?
    private var pendingCreation: PendingConversationRequest?
    private var replayContinuation: AsyncThrowingStream<(Int?,ServerEvent),Error>.Continuation?
    private var armedGates = Set<String>()
    private var gates: [String: CheckedContinuation<Void,Never>] = [:]
    private var arrivals: [String: [CheckedContinuation<Void,Never>]] = [:]
    private var requests: [PendingTurnRequest] = []
    private var creationBodies: [Data] = []
    private var creationPersistedBeforePOST: [Bool] = []
    private var replays: [ReplayCall] = []
    private var failNextStream = false
    private var failNextCreation = false
    private var recoveryFailure: APIError?
    private var recoveryCalls = 0
    private var configFailures = 0
    private var bootstrapCalls = 0
    private var favoritesResponse = Data(#"{"items":[]}"#.utf8)
    private var historyResponse = Data(#"{"items":[]}"#.utf8)
    private var conversationResponses: [String: Data] = [:]

    // Gates acknowledge their arrival before tests release them. No wall-clock sleeps.
    func arm(_ key: String) { armedGates.insert(key) }
    func waitForGate(_ key: String) async {
        if gates[key] != nil || key == "replay" && replayContinuation != nil { return }
        await withCheckedContinuation { arrivals[key, default: []].append($0) }
    }
    private func announce(_ key: String) { arrivals.removeValue(forKey: key)?.forEach { $0.resume() } }
    private func blockIfArmed(_ key: String) async {
        guard armedGates.remove(key) != nil else { return }
        await withCheckedContinuation { continuation in
            gates[key] = continuation
            announce(key)
        }
    }
    func release(_ key: String) { gates.removeValue(forKey: key)?.resume() }

    func bootstrap() async throws { bootstrapCalls += 1 }
    func cacheOwnerKey() -> String? { "endpoint:user" }
    func savePending(_ request: PendingTurnRequest) throws { pending = request }
    func clearPending(ifMatching id: UUID) throws { if pending?.clientTurnId == id { pending = nil } }
    func loadPending() throws -> PendingTurnRequest? { pending }
    func savePendingConversation(_ request: PendingConversationRequest) throws { pendingCreation = request }
    func clearPendingConversation(ifMatching id: UUID) throws {
        if pendingCreation?.clientConversationId == id { pendingCreation = nil }
    }
    func loadPendingConversation() throws -> PendingConversationRequest? { pendingCreation }
    func failStreamOnce() { failNextStream = true }
    func failCreationOnce() { failNextCreation = true }
    func failConfigOnce() { configFailures = 1 }
    func failRecovery(_ error: APIError) { recoveryFailure = error }
    func recoveryCount() -> Int { recoveryCalls }
    func enrollmentCount() -> Int { bootstrapCalls }
    func sentRequests() -> [PendingTurnRequest] { requests }
    func sentBodies() throws -> [Data] { try requests.map { try $0.body() } }
    func createdBodies() -> [Data] { creationBodies }
    func creationWasDurableBeforeEveryPOST() -> Bool {
        !creationPersistedBeforePOST.isEmpty && creationPersistedBeforePOST.allSatisfy { $0 }
    }
    func replayCalls() -> [ReplayCall] { replays }
    func setFavoritesResponse(_ data: Data) { favoritesResponse = data }
    func setHistoryResponse(_ data: Data) { historyResponse = data }
    func setConversationResponse(_ path: String, data: Data) { conversationResponses[path] = data }
    func deliverReplay(message: String = "Old answer", sequence: Int = 2) {
        replayContinuation?.yield((sequence, .message(message, [])))
        replayContinuation?.yield((sequence, .message(message, [])))
        replayContinuation?.yield((sequence + 1, .done("complete")))
        replayContinuation?.finish()
        replayContinuation = nil
    }
    func stream(request: PendingTurnRequest) async throws -> AsyncThrowingStream<(Int?,ServerEvent),Error> {
        requests.append(request)
        if failNextStream { failNextStream = false; throw APIError.http(503,"temporary") }
        return AsyncThrowingStream { continuation in
            continuation.yield((nil, .accepted(turnID: "stable-turn")))
            continuation.yield((1, .accepted(turnID: "stable-turn")))
            continuation.yield((2, .message("One answer", [])))
            continuation.yield((2, .message("One answer", [])))
            continuation.yield((3, .done("complete")))
            continuation.finish()
        }
    }
    func replay(conversationID: String, turnID: String, after: Int) async throws -> AsyncThrowingStream<(Int?,ServerEvent),Error> {
        replays.append(ReplayCall(conversationID: conversationID, turnID: turnID, after: after))
        return AsyncThrowingStream { continuation in
            replayContinuation = continuation
            announce("replay")
        }
    }
    func call<T: Decodable & Sendable>(_ path: String, method: String = "GET", body: Data? = nil, authenticated: Bool = true) async throws -> T {
        let data: Data
        if path == "v1/config" {
            if configFailures > 0 { configFailures -= 1; throw APIError.http(503,"temporary") }
            data = Data(#"{"mode":"live","ai_enabled":false,"cities":["Астана"],"retention_days":90,"capabilities":["idempotent_conversation_create","account_recovery"]}"#.utf8)
        } else if path == "v1/favorites" {
            data = favoritesResponse
            await blockIfArmed("favorites-read")
        } else if path == "v1/conversations" && method == "GET" {
            data = historyResponse
            await blockIfArmed("history-read")
        } else if path == "v1/conversations" && method == "POST" {
            let posted = body ?? Data()
            creationBodies.append(posted)
            creationPersistedBeforePOST.append((try? pendingCreation?.body()) == posted)
            if failNextCreation { failNextCreation = false; throw APIError.http(503,"response_lost") }
            data = Data(#"{"id":"created","title":"Created","preferences":{}}"#.utf8)
        } else if path.hasSuffix("/preferences") {
            data = body ?? Data("{}".utf8)
        } else {
            let id = path.split(separator: "/").last.map(String.init) ?? "old"
            data = conversationResponses[path] ?? Data("{\"id\":\"\(id)\",\"title\":\"History\",\"preferences\":{\"city\":\"Old city\"},\"turns\":[]}".utf8)
            await blockIfArmed("conversation:" + path)
        }
        return try APIJSON.decoder().decode(T.self, from: data)
    }
    func perform(_ path: String, method: String) async throws {
        if path.hasPrefix("v1/favorites/") { await blockIfArmed("favorite-mutation") }
    }
    func generateRecoveryCode() async throws -> String { String(repeating: "r", count: 43) }
    func recoverAccount(code: String) async throws {
        recoveryCalls += 1
        if let recoveryFailure { throw recoveryFailure }
        pending = nil; pendingCreation = nil
    }
    func resetSession() throws { pending = nil; pendingCreation = nil }
    func deleteAccount() async throws { pending = nil; pendingCreation = nil }
}
''')
(root/'Tests/MobileStoreTests/MobileStoreTests.swift').write_text(r'''import Foundation
import SwiftData
import Testing
import MekenCore
@testable import MobileStore

@MainActor private func spinUntil(_ condition: @escaping () async -> Bool) async throws {
    for _ in 0..<10000 {
        if await condition() { return }
        await Task.yield()
    }
    throw HarnessError.timeout
}
private enum HarnessError: Error { case timeout }
private let apartmentRaw = #"{"id":"listing","provider_id":"provider","provider_name":"Provider","complex_name":"Complex","city":"Астана","district":"","address":"Address","rooms":2,"area_m2":50,"floor":2,"total_floors":10,"price_kzt":35000000,"status":"available","finish":"","completion":"","source_url":"https://example.com","observed_at":"2026-10-04T00:00:00Z","version":1,"provenance":"provider","freshness":"recent","reasons":[],"tradeoffs":[],"amenities":[]}"#
private func apartment() throws -> Apartment { try APIJSON.decoder().decode(Apartment.self, from: Data(apartmentRaw.utf8)) }
private func config(capabilities: [String]) throws -> AppConfig {
    let encodedCapabilities = String(decoding: try APIJSON.encoder().encode(capabilities), as: UTF8.self)
    return try APIJSON.decoder().decode(AppConfig.self, from: Data("{\"mode\":\"live\",\"ai_enabled\":false,\"cities\":[],\"retention_days\":90,\"capabilities\":\(encodedCapabilities)}".utf8))
}
@MainActor private func memoryContainer() throws -> ModelContainer {
    try ModelContainer(for: CachedFavorite.self, configurations: ModelConfiguration(isStoredInMemoryOnly: true))
}

@Test @MainActor func replacedReplayCannotChangeNewSearch() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    store.isReady = true
    store.conversationID = "old"
    store.search.turnID = "old-turn"
    let replay = Task { await store.resume() }
    await api.waitForGate("replay")
    store.newConversation()
    await api.deliverReplay()
    await replay.value
    #expect(store.conversationID == nil)
    #expect(store.search.messages.isEmpty)
    #expect(!store.search.isStreaming)
}

@Test @MainActor func replacedHistoryResponseCannotRestoreOldConversation() async throws {
    let api = APIClient()
    await api.arm("conversation:v1/conversations/old")
    let store = AppStore(api: api)
    store.isReady = true
    let load = Task { try await store.restoreConversation("old") }
    await api.waitForGate("conversation:v1/conversations/old")
    store.newConversation()
    store.search.preferences.city = "New city"
    await api.release("conversation:v1/conversations/old")
    do { try await load.value; Issue.record("Expected cancelled stale history") }
    catch is CancellationError {}
    #expect(store.conversationID == nil)
    #expect(store.search.preferences.city == "New city")
}

@Test @MainActor func ambiguousPostRetriesSameBodyAndDeduplicatesReplay() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    store.isReady = true
    store.conversationID = "created"
    await api.failStreamOnce()
    store.send("Test message", selected: ["listing"])
    try await spinUntil { !store.search.isStreaming }
    #expect(store.pendingRequest != nil)
    await store.resume()
    let bodies = try await api.sentBodies()
    #expect(bodies.count == 2)
    #expect(bodies[0] == bodies[1])
    #expect(store.search.messages.filter { !$0.isUser }.count == 1)
    #expect(store.search.messages.filter { $0.isUser }.count == 1)
    #expect(store.pendingRequest == nil)
    #expect(!store.search.isStreaming)
    #expect(store.search.error == nil)
}

@Test @MainActor func cachedOfflineStartupCanReconnectAndCacheIsScoped() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    let container = try memoryContainer()
    let context = container.mainContext
    let value = try apartment()
    context.insert(try CachedFavorite(apartment: value, ownerKey: "endpoint:user"))
    context.insert(try CachedFavorite(apartment: value, ownerKey: "endpoint:other-user"))
    try context.save()
    await api.failConfigOnce()
    await store.initialize(context: context)
    #expect(store.isReady)
    #expect(store.favorites.count == 1)
    #expect(store.needsConnectionRecovery)
    await store.retryInitialization()
    #expect(store.isReady)
    #expect(!store.needsConnectionRecovery)
    #expect(await api.enrollmentCount() == 2)
}

@Test @MainActor func lateFavoriteCannotRepopulateDeletedIdentity() async throws {
    let api = APIClient()
    await api.arm("favorite-mutation")
    let store = AppStore(api: api)
    store.isReady = true
    let value = try apartment()
    let favorite = Task { await store.toggleFavorite(value) }
    await api.waitForGate("favorite-mutation")
    try await store.deleteAccount()
    await api.release("favorite-mutation")
    await favorite.value
    #expect(store.favorites.isEmpty)
    #expect(store.isMutatingFavorite.isEmpty)
    #expect(store.favoriteError == nil)
    #expect(!store.isReady)
}

@Test @MainActor func lostCreationResponseRetriesOriginalSnapshotAndFirstTurn() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    store.isReady = true
    store.config = try config(capabilities: ["idempotent_conversation_create"])
    var preferences = Preferences(city: "Астана", budgetMax: 35_000_000)
    preferences.requiredAmenities = ["school"]
    store.search.preferences = preferences
    await api.failCreationOnce()
    store.send("Рядом со школой", selected: ["listing"])
    try await spinUntil { !store.search.isStreaming }
    let original = try #require(store.pendingConversation)
    #expect(original.preferences == preferences)
    #expect(original.message == "Рядом со школой")
    #expect(original.selectedListingIds == ["listing"])
    #expect(original.usesIdempotencyKey)
    #expect(store.pendingRequest == nil)
    store.search.preferences = Preferences(city: "Алматы", budgetMax: 60_000_000)
    await store.resume()
    let bodies = await api.createdBodies()
    #expect(bodies.count == 2)
    #expect(bodies[0] == bodies[1])
    #expect(bodies[0] == (try original.body()))
    #expect(await api.creationWasDurableBeforeEveryPOST())
    let turns = await api.sentRequests()
    #expect(turns.count == 1)
    #expect(turns.first == original.turnRequest(conversationID: "created"))
    #expect(store.search.messages.filter { $0.isUser }.map(\.text) == [original.message])
    #expect(store.pendingConversation == nil)
    #expect(store.pendingRequest == nil)
}

@Test @MainActor func legacyCreationOmitsUnsupportedKeyAndCannotBeAmbiguouslyRetried() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    store.isReady = true
    store.config = try config(capabilities: [])
    await api.failCreationOnce()
    store.send("Legacy first send")
    try await spinUntil { !store.search.isStreaming }
    let creation = try #require(store.pendingConversation)
    #expect(!creation.usesIdempotencyKey)
    let bodies = await api.createdBodies()
    #expect(bodies.count == 1)
    let posted = try #require(JSONSerialization.jsonObject(with: bodies[0]) as? [String: Any])
    #expect(Set(posted.keys) == ["preferences"])
    await store.resume()
    #expect(await api.createdBodies().count == 1)
    #expect(await api.sentRequests().isEmpty)
    #expect(store.pendingConversation == creation)
    #expect(!store.search.isStreaming)
    #expect(store.search.error != nil)
}

@Test @MainActor func restartedStoreLoadsDurableCreationBeforeResumingFirstSend() async throws {
    let api = APIClient()
    let pending = PendingConversationRequest(
        preferences: Preferences(city: "Астана", budgetMax: 42_000_000),
        message: "Сохранённое намерение", selectedListingIds: ["listing"]
    )
    try await api.savePendingConversation(pending)
    let restarted = AppStore(api: api)
    let container = try memoryContainer()
    await restarted.initialize(context: container.mainContext)
    #expect(restarted.isReady)
    #expect(restarted.pendingConversation == pending)
    #expect(restarted.conversationID == nil)
    #expect(restarted.search.preferences == pending.preferences)
    #expect(restarted.search.messages.map(\.text) == [pending.message])
    #expect(await api.createdBodies().isEmpty)
    await restarted.resume()
    #expect(await api.createdBodies() == [try pending.body()])
    #expect(await api.sentRequests() == [pending.turnRequest(conversationID: "created")])
    #expect(try await api.loadPendingConversation() == nil)
    #expect(try await api.loadPending() == nil)
    #expect(restarted.search.messages.filter { $0.isUser }.map(\.text) == [pending.message])
}

@Test @MainActor func olderHistoryPrependsUniqueMessagesWithoutReplacingLatestStateOrReplayCursor() async throws {
    let api = APIClient()
    let latest = """
    {"id":"paged","title":"Paged","preferences":{"city":"Current city","budget_max":45000000},"turns":[
      {"id":"latest-turn","message":"Latest question","status":"complete","events":[
        {"sequence":5,"kind":"message","payload":{"text":"Latest answer","citations":[]}},
        {"sequence":7,"kind":"listings","payload":{"items":[\(apartmentRaw)]}},
        {"sequence":9,"kind":"preferences","payload":{"city":"Current city","budget_max":45000000}},
        {"sequence":11,"kind":"done","payload":{"status":"complete"}}]}],
      "has_more":true,"next_before":"latest-turn"}
    """
    let earlier = """
    {"id":"paged","title":"Paged","preferences":{"city":"Old city"},"turns":[
      {"id":"older-turn","message":"Older question","status":"complete","events":[
        {"sequence":8,"kind":"message","payload":{"text":"Older answer two","citations":[]}},
        {"sequence":2,"kind":"message","payload":{"text":"Older answer one","citations":[]}},
        {"sequence":3,"kind":"preferences","payload":{"city":"Old city"}},
        {"sequence":4,"kind":"listings","payload":{"items":[]}},
        {"sequence":5,"kind":"notice","payload":{"text":"Outdated notice"}}]},
      {"id":"older-turn","message":"Duplicate old","status":"complete","events":[]},
      {"id":"latest-turn","message":"Duplicate latest","status":"complete","events":[]}],
      "has_more":false,"next_before":null}
    """
    await api.setConversationResponse("v1/conversations/paged", data: Data(latest.utf8))
    await api.setConversationResponse("v1/conversations/paged?before=latest-turn", data: Data(earlier.utf8))
    let store = AppStore(api: api)
    store.isReady = true
    try await store.restoreConversation("paged")
    let currentMessages = store.search.messages
    let currentApartments = store.search.apartments
    let currentPreferences = store.search.preferences
    let currentNotice = store.search.notice
    #expect(store.hasOlderMessages)
    await store.loadOlderMessages()
    #expect(store.search.messages.map(\.text) == ["Older question", "Older answer one", "Older answer two", "Latest question", "Latest answer"])
    #expect(Array(store.search.messages.suffix(currentMessages.count)) == currentMessages)
    #expect(store.search.preferences == currentPreferences)
    #expect(store.search.apartments == currentApartments)
    #expect(store.search.notice == currentNotice)
    #expect(store.search.turnID == "latest-turn")
    #expect(!store.hasOlderMessages)
    #expect(store.historyLoadError == nil)
    let replay = Task { await store.resume() }
    await api.waitForGate("replay")
    let calls = await api.replayCalls()
    #expect(calls.last?.after == 11)
    #expect(calls.last?.conversationID == "paged")
    #expect(calls.last?.turnID == "latest-turn")
    await api.deliverReplay(message: "Fresh answer", sequence: 12)
    await replay.value
    #expect(store.search.messages.filter { $0.text == "Fresh answer" }.count == 1)
}

@Test @MainActor func restoringConversationBlocksSendingIntoPreviouslySelectedConversation() async throws {
    let api = APIClient()
    await api.arm("conversation:v1/conversations/new")
    let store = AppStore(api: api)
    store.isReady = true
    store.conversationID = "old"
    let load = Task { try await store.restoreConversation("new") }
    await api.waitForGate("conversation:v1/conversations/new")
    #expect(store.isRestoringConversation)
    #expect(store.isPreparingSearch)
    store.send("Must not go to old conversation")
    #expect(await api.sentRequests().isEmpty)
    #expect(await api.createdBodies().isEmpty)
    #expect(store.pendingRequest == nil)
    #expect(store.search.messages.isEmpty)
    await api.release("conversation:v1/conversations/new")
    try await load.value
    #expect(store.conversationID == "new")
    #expect(!store.isPreparingSearch)
}

@Test @MainActor func staleFavoritesGETCannotUndoACompletedToggle() async throws {
    let api = APIClient()
    await api.setFavoritesResponse(Data("{\"items\":[\(apartmentRaw)]}".utf8))
    await api.arm("favorites-read")
    let store = AppStore(api: api)
    store.isReady = true
    store.config = try config(capabilities: [])
    let value = try apartment()
    store.favorites = [value]
    let refresh = Task { await store.loadFavorites() }
    await api.waitForGate("favorites-read")
    await store.toggleFavorite(value)
    #expect(store.favorites.isEmpty)
    await api.release("favorites-read")
    await refresh.value
    #expect(store.favorites.isEmpty)
    #expect(store.favoriteError == nil)
    #expect(store.isMutatingFavorite.isEmpty)
}

@Test @MainActor func staleHistoryGETCannotReinsertDeletedConversation() async throws {
    let api = APIClient()
    let summaryRaw = #"{"id":"deleted","title":"Deleted","preferences":{}}"#
    await api.setHistoryResponse(Data("{\"items\":[\(summaryRaw)]}".utf8))
    await api.arm("history-read")
    let store = AppStore(api: api)
    store.isReady = true
    store.conversations = [try APIJSON.decoder().decode(ConversationSummary.self, from: Data(summaryRaw.utf8))]
    let refresh = Task { await store.refreshHistory() }
    await api.waitForGate("history-read")
    try await store.deleteConversation("deleted")
    #expect(store.conversations.isEmpty)
    await api.release("history-read")
    await refresh.value
    #expect(store.conversations.isEmpty)
    #expect(store.search.error == nil)
}

@Test @MainActor func failedAccountRecoveryPreservesExistingProfileAndCapabilityGate() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    store.isReady = true
    store.conversationID = "existing"
    store.favorites = [try apartment()]
    store.search.preferences = Preferences(city: "Астана", budgetMax: 35_000_000)
    store.search.messages = [ChatMessage(isUser: true, text: "Existing question")]
    let originalFavorites = store.favorites
    let originalMessages = store.search.messages
    let originalPreferences = store.search.preferences
    let code = String(repeating: "r", count: 43)
    store.config = try config(capabilities: [])
    #expect(!store.supportsAccountRecovery)
    do { try await store.recoverAccount(code: code); Issue.record("Expected unsupported recovery") }
    catch APIError.invalidConfiguration {}
    #expect(await api.recoveryCount() == 0)

    store.config = try config(capabilities: ["account_recovery"])
    #expect(store.supportsAccountRecovery)
    await api.failRecovery(.http(401, "invalid_recovery_code"))
    do { try await store.recoverAccount(code: code); Issue.record("Expected invalid recovery code") }
    catch APIError.http(401, "invalid_recovery_code") {}
    #expect(await api.recoveryCount() == 1)
    #expect(store.isReady)
    #expect(!store.isDeleting)
    #expect(!store.requiresSessionReset)
    #expect(store.conversationID == "existing")
    #expect(store.favorites == originalFavorites)
    #expect(store.search.messages == originalMessages)
    #expect(store.search.preferences == originalPreferences)
}

@Test @MainActor func unknownConfigurationStillAllowsExplicitRecoveryWithoutResettingProfile() async throws {
    let api = APIClient()
    let store = AppStore(api: api)
    #expect(!store.supportsAccountRecovery)
    #expect(store.canOfferAccountRecovery)
    await api.failRecovery(.http(404, "account_recovery_unavailable"))
    do { try await store.recoverAccount(code: String(repeating: "r", count: 43)); Issue.record("Expected unavailable recovery") }
    catch APIError.http(404, "account_recovery_unavailable") {}
    #expect(await api.recoveryCount() == 1)
    #expect(!store.requiresSessionReset)
    #expect(!store.isDeleting)
    #expect(store.initializationError != nil)
    #expect(store.needsConnectionRecovery)
}
''')

environment = os.environ.copy()
environment["SWIFTPM_MODULECACHE_OVERRIDE"] = str(root / "module-cache")
environment["CLANG_MODULE_CACHE_PATH"] = str(root / "module-cache")
try:
    result = subprocess.run([
        "swift", "test", "--disable-sandbox", "--package-path", str(root),
        "--cache-path", str(root / "cache"),
        "--config-path", str(root / "config"),
        "--security-path", str(root / "security"),
    ], env=environment, check=False)
finally:
    shutil.rmtree(root)
raise SystemExit(result.returncode)
