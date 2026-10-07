import Foundation
import Testing
@testable import MekenCore

@Test func pendingConversationRoundtripPreservesTheCompleteFirstSendIntent() throws {
    var currentPreferences = Preferences(city: "Астана", budgetMax: 35_000_000)
    currentPreferences.rooms = [2, 3]
    currentPreferences.requiredAmenities = ["school"]
    let original = PendingConversationRequest(
        clientConversationId: UUID(uuidString: "66C6276B-4FD2-4FDE-9D5C-A2C82E602DA1")!,
        preferences: currentPreferences,
        firstTurnId: UUID(uuidString: "21A3B4CC-637B-48A4-9AAA-3232E8E136AC")!,
        message: "Рядом со школой 🏠",
        selectedListingIds: ["listing-2", "listing-1"]
    )
    let stored = try APIJSON.encoder().encode(original)
    currentPreferences.city = "Алматы"
    currentPreferences.budgetMax = 50_000_000
    currentPreferences.requiredAmenities = []

    let restored = try APIJSON.decoder().decode(PendingConversationRequest.self, from: stored)
    #expect(restored == original)
    #expect(restored.preferences != currentPreferences)
    #expect(try restored.body() == original.body())
    let turn = restored.turnRequest(conversationID: "created-conversation")
    #expect(turn.clientTurnId == original.firstTurnId)
    #expect(turn.conversationId == "created-conversation")
    #expect(turn.message == original.message)
    #expect(turn.selectedListingIds == original.selectedListingIds)
    #expect(try turn.body() == original.turnRequest(conversationID: "created-conversation").body())
}

@Test func conversationBodyExposesOnlyCreationFieldsWithStableOrdering() throws {
    let request = PendingConversationRequest(
        clientConversationId: UUID(uuidString: "66C6276B-4FD2-4FDE-9D5C-A2C82E602DA1")!,
        preferences: Preferences(city: "Astana", budgetMax: 35_000_000),
        firstTurnId: UUID(uuidString: "21A3B4CC-637B-48A4-9AAA-3232E8E136AC")!,
        message: "Find an apartment",
        selectedListingIds: ["listing-1"]
    )
    let expected = #"{"client_conversation_id":"66C6276B-4FD2-4FDE-9D5C-A2C82E602DA1","preferences":{"amenity_radius_m":1000,"amenity_scope":"nearby","budget_max":35000000,"city":"Astana","preferred_amenities":[],"required_amenities":[],"rooms":[]}}"#
    #expect(String(decoding: try request.body(), as: UTF8.self) == expected)
}

@Test func legacyConversationSnapshotPersistsItsUnkeyedCreationBody() throws {
    let request = PendingConversationRequest(
        preferences: Preferences(city: "Astana", budgetMax: 35_000_000),
        message: "Find an apartment",
        usesIdempotencyKey: false
    )
    let stored = try APIJSON.encoder().encode(request)
    let restored = try APIJSON.decoder().decode(PendingConversationRequest.self, from: stored)
    #expect(restored == request)
    #expect(!restored.usesIdempotencyKey)
    let expected = #"{"preferences":{"amenity_radius_m":1000,"amenity_scope":"nearby","budget_max":35000000,"city":"Astana","preferred_amenities":[],"required_amenities":[],"rooms":[]}}"#
    #expect(String(decoding: try restored.body(), as: UTF8.self) == expected)
    #expect(try restored.body() == request.body())
}

@Test func existingConversationSnapshotWithoutCompatibilityFlagKeepsIdempotency() throws {
    let request = PendingConversationRequest(
        preferences: Preferences(city: "Astana"),
        message: "Find an apartment"
    )
    let stored = try APIJSON.encoder().encode(request)
    var oldSnapshot = try #require(JSONSerialization.jsonObject(with: stored) as? [String: Any])
    oldSnapshot.removeValue(forKey: "uses_idempotency_key")
    let oldData = try JSONSerialization.data(withJSONObject: oldSnapshot)
    let restored = try APIJSON.decoder().decode(PendingConversationRequest.self, from: oldData)
    #expect(restored == request)
    #expect(restored.usesIdempotencyKey)
    #expect(try restored.body() == request.body())
}

@Test func appConfigWithoutCapabilitiesSupportsOlderServerResponses() throws {
    let oldData = Data(#"{"mode":"live","ai_enabled":true,"cities":["Astana"],"retention_days":30}"#.utf8)
    let oldConfig = try APIJSON.decoder().decode(AppConfig.self, from: oldData)
    #expect(oldConfig.capabilities.isEmpty)
    let currentData = Data(#"{"mode":"live","ai_enabled":true,"cities":["Astana"],"retention_days":30,"capabilities":["idempotent_conversation_create"]}"#.utf8)
    let currentConfig = try APIJSON.decoder().decode(AppConfig.self, from: currentData)
    #expect(currentConfig.capabilities == ["idempotent_conversation_create"])
}

@Test func pendingTurnRoundtripKeepsOriginalIdempotencyKeyAndPayload() throws {
    let original = PendingTurnRequest(
        clientTurnId: UUID(uuidString: "21A3B4CC-637B-48A4-9AAA-3232E8E136AC")!,
        conversationId: "conversation-123",
        message: "Астана 🏠",
        selectedListingIds: ["listing-2", "listing-1"]
    )
    let stored = try APIJSON.encoder().encode(original)
    let restored = try APIJSON.decoder().decode(PendingTurnRequest.self, from: stored)
    #expect(restored == original)
    #expect(try restored.body() == original.body())

    let persisted = try #require(JSONSerialization.jsonObject(with: stored) as? [String: Any])
    #expect(persisted["conversation_id"] as? String == "conversation-123")
    #expect(persisted["client_turn_id"] as? String == original.clientTurnId.uuidString)
}

@Test func turnBodyUsesOnlyAPIFieldsAndStableOrdering() throws {
    let request = PendingTurnRequest(
        clientTurnId: UUID(uuidString: "21A3B4CC-637B-48A4-9AAA-3232E8E136AC")!,
        conversationId: "conversation-123",
        message: "Find an apartment",
        selectedListingIds: ["listing-2", "listing-1"]
    )
    let expected = #"{"client_turn_id":"21A3B4CC-637B-48A4-9AAA-3232E8E136AC","message":"Find an apartment","selected_listing_ids":["listing-2","listing-1"]}"#
    #expect(String(decoding: try request.body(), as: UTF8.self) == expected)
}

@Test func sequenceCursorDeduplicatesReplayWithoutSuppressingEphemeralEvents() {
    var cursor = SequenceCursor()
    let cases: [(Int?, Bool, Int)] = [
        (nil, true, 0), (0, false, 0), (-1, false, 0),
        (4, true, 4), (4, false, 4), (3, false, 4),
        (nil, true, 4), (7, true, 7)
    ]
    for (candidate, expectedAcceptance, expectedSequence) in cases {
        let accepted = cursor.accept(candidate)
        #expect(accepted == expectedAcceptance)
        #expect(cursor.sequence == expectedSequence)
    }
}

@Test func sequenceCursorCanResumeAndResetForAnotherConversation() {
    var cursor = SequenceCursor(sequence: 12)
    let replay = cursor.accept(12)
    let nextEvent = cursor.accept(13)
    #expect(!replay)
    #expect(nextEvent)
    cursor = SequenceCursor()
    let firstEvent = cursor.accept(1)
    #expect(firstEvent)
    #expect(cursor.sequence == 1)

    var invalidResume = SequenceCursor(sequence: -5)
    #expect(invalidResume.sequence == 0)
    let invalidEvent = invalidResume.accept(-4)
    #expect(!invalidEvent)
}

@Test func historyPaginationMetadataKeepsOlderResponsesCompatible() throws {
    let data = Data(#"{"id":"conversation","title":"History","preferences":{},"turns":[]}"#.utf8)
    let history = try APIJSON.decoder().decode(ConversationHistory.self, from: data)
    #expect(!history.hasMore)
    #expect(history.nextBefore == nil)
}

@Test func historyPaginationMetadataReadsServerCursor() throws {
    let data = Data(#"{"id":"conversation","title":"History","preferences":{},"turns":[],"has_more":true,"next_before":"earlier-turn"}"#.utf8)
    let history = try APIJSON.decoder().decode(ConversationHistory.self, from: data)
    #expect(history.hasMore)
    #expect(history.nextBefore == "earlier-turn")
}
