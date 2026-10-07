import Foundation

/// Captures the complete first-send intent before the conversation creation request.
public struct PendingConversationRequest: Codable, Equatable, Sendable {
    public let clientConversationId: UUID
    public let preferences: Preferences
    public let firstTurnId: UUID
    public let message: String
    public let selectedListingIds: [String]
    public let usesIdempotencyKey: Bool

    public init(
        clientConversationId: UUID = UUID(),
        preferences: Preferences,
        firstTurnId: UUID = UUID(),
        message: String,
        selectedListingIds: [String] = [],
        usesIdempotencyKey: Bool = true
    ) {
        self.clientConversationId = clientConversationId
        self.preferences = preferences
        self.firstTurnId = firstTurnId
        self.message = message
        self.selectedListingIds = selectedListingIds
        self.usesIdempotencyKey = usesIdempotencyKey
    }

    private enum CodingKeys: String, CodingKey {
        case clientConversationId, preferences, firstTurnId, message, selectedListingIds, usesIdempotencyKey
    }

    public init(from decoder: Decoder) throws {
        let values = try decoder.container(keyedBy: CodingKeys.self)
        clientConversationId = try values.decode(UUID.self, forKey: .clientConversationId)
        preferences = try values.decode(Preferences.self, forKey: .preferences)
        firstTurnId = try values.decode(UUID.self, forKey: .firstTurnId)
        message = try values.decode(String.self, forKey: .message)
        selectedListingIds = try values.decode([String].self, forKey: .selectedListingIds)
        usesIdempotencyKey = try values.decodeIfPresent(Bool.self, forKey: .usesIdempotencyKey) ?? true
    }

    public func body() throws -> Data {
        let encoder = APIJSON.encoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(ConversationBody(
            clientConversationId: usesIdempotencyKey ? clientConversationId : nil,
            preferences: preferences
        ))
    }

    public func turnRequest(conversationID: String) -> PendingTurnRequest {
        PendingTurnRequest(
            clientTurnId: firstTurnId,
            conversationId: conversationID,
            message: message,
            selectedListingIds: selectedListingIds
        )
    }

    private struct ConversationBody: Encodable {
        let clientConversationId: UUID?
        let preferences: Preferences
    }
}

/// A durable request snapshot: retries keep the same idempotency key and payload.
public struct PendingTurnRequest: Codable, Equatable, Sendable {
    public let clientTurnId: UUID
    public let conversationId: String
    public let message: String
    public let selectedListingIds: [String]

    public init(
        clientTurnId: UUID = UUID(),
        conversationId: String,
        message: String,
        selectedListingIds: [String] = []
    ) {
        self.clientTurnId = clientTurnId
        self.conversationId = conversationId
        self.message = message
        self.selectedListingIds = selectedListingIds
    }

    /// The conversation belongs in the URL, not in the turn request body.
    public func body() throws -> Data {
        let encoder = APIJSON.encoder()
        encoder.outputFormatting = [.sortedKeys]
        return try encoder.encode(TurnBody(
            clientTurnId: clientTurnId,
            message: message,
            selectedListingIds: selectedListingIds
        ))
    }

    private struct TurnBody: Encodable {
        let clientTurnId: UUID
        let message: String
        let selectedListingIds: [String]
    }
}

/// Tracks durable SSE events while allowing events without a replay sequence.
public struct SequenceCursor: Equatable, Sendable {
    public private(set) var sequence: Int

    public init(sequence: Int = 0) {
        self.sequence = max(0, sequence)
    }

    public mutating func accept(_ candidate: Int?) -> Bool {
        guard let candidate else { return true }
        guard candidate > 0, candidate > sequence else { return false }
        sequence = candidate
        return true
    }
}
