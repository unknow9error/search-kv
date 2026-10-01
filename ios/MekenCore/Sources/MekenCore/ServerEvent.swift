import Foundation

public enum ServerEvent: Sendable {
    case accepted(turnID: String)
    case status(String)
    case notice(String)
    case preferences(Preferences)
    case listings([Apartment])
    case message(String, [Citation])
    case suggestions([String])
    case provider(String, String)
    case done(String)
    case pending
    case failure(String)
    case ignored

    public static func decode(kind: String, data: Data) throws -> ServerEvent {
        let decoder = APIJSON.decoder()
        struct TextPayload: Decodable { let text: String }
        struct Accepted: Decodable { let turnId: String }
        struct Message: Decodable { let text: String; let citations: [Citation] }
        struct Provider: Decodable { let name: String; let status: String }
        struct Done: Decodable { let status: String }
        switch kind {
        case "accepted": return .accepted(turnID: try decoder.decode(Accepted.self, from: data).turnId)
        case "status": return .status(try decoder.decode(TextPayload.self, from: data).text)
        case "notice": return .notice(try decoder.decode(TextPayload.self, from: data).text)
        case "error": return .failure(try decoder.decode(TextPayload.self, from: data).text)
        case "preferences": return .preferences(try decoder.decode(Preferences.self, from: data))
        case "listings": return .listings(try decoder.decode(Items<Apartment>.self, from: data).items)
        case "message":
            let value = try decoder.decode(Message.self, from: data)
            return .message(value.text, value.citations)
        case "suggestions": return .suggestions(try decoder.decode(Items<String>.self, from: data).items)
        case "provider":
            let value = try decoder.decode(Provider.self, from: data)
            return .provider(value.name, value.status)
        case "done": return .done(try decoder.decode(Done.self, from: data).status)
        case "pending": return .pending
        default: return .ignored
        }
    }
}

public struct SSEFrame: Equatable, Sendable {
    public let id: Int?
    public let event: String
    public let data: String
}

public enum StreamError: Error { case oversizedFrame, interrupted }

/// Parses SSE without assuming network chunk boundaries correspond to events.
public struct SSEParser: Sendable {
    private var event = "message"
    private var id: Int?
    private var data: [String] = []
    private var size = 0
    public init() {}
    public mutating func consume(line: String) throws -> SSEFrame? {
        let line = line.hasSuffix("\r") ? String(line.dropLast()) : line
        if line.isEmpty {
            defer { event = "message"; id = nil; data = []; size = 0 }
            return data.isEmpty ? nil : SSEFrame(id: id, event: event, data: data.joined(separator: "\n"))
        }
        size += line.utf8.count
        guard size <= 2_000_000 else { throw StreamError.oversizedFrame }
        guard !line.hasPrefix(":") else { return nil }
        let parts = line.split(separator: ":", maxSplits: 1, omittingEmptySubsequences: false)
        let key = String(parts[0])
        var value = parts.count == 2 ? String(parts[1]) : ""
        if value.hasPrefix(" ") { value.removeFirst() }
        switch key {
        case "id": id = Int(value)
        case "event": event = value
        case "data": data.append(value)
        default: break
        }
        return nil
    }
}

/// URLSession.AsyncBytes.lines drops empty lines on supported runtimes.
/// SSE depends on those delimiters, so frame the raw bytes instead.
public struct SSEByteParser: Sendable {
    private var parser = SSEParser()
    private var line: [UInt8] = []
    private var previousCR = false
    private var isFirstLine = true
    public init() {}

    public mutating func consume(byte: UInt8) throws -> SSEFrame? {
        if byte == 10 && previousCR { previousCR = false; return nil }
        previousCR = byte == 13
        if byte == 10 || byte == 13 {
            var text = String(decoding: line, as: UTF8.self)
            line.removeAll(keepingCapacity: true)
            if isFirstLine {
                isFirstLine = false
                if text.hasPrefix("\u{feff}") { text.removeFirst() }
            }
            return try parser.consume(line: text)
        }
        line.append(byte)
        guard line.count <= 2_000_000 else { throw StreamError.oversizedFrame }
        return nil
    }
}

public struct ChatMessage: Identifiable, Equatable, Sendable {
    public let id: UUID
    public let isUser: Bool
    public let text: String
    public let citations: [Citation]
    public init(isUser: Bool, text: String, citations: [Citation] = []) {
        id = UUID(); self.isUser = isUser; self.text = text; self.citations = citations
    }
}

public struct SearchState: Sendable {
    public var preferences = Preferences()
    public var apartments: [Apartment] = []
    public var messages: [ChatMessage] = []
    public var suggestions: [String] = []
    public var status = ""
    public var notice: String?
    public var error: String?
    public var isStreaming = false
    public var turnID: String?
    public var isPending = false
    public init() {}
    public mutating func apply(_ event: ServerEvent) {
        switch event {
        case .accepted(let id): turnID = id
        case .status(let text): status = text
        case .notice(let text): notice = text
        case .preferences(let value): preferences = value
        case .listings(let items):
            var seen = Set<String>()
            apartments = items.filter { seen.insert($0.id).inserted && $0.status == "available" }
        case .message(let text, let citations): messages.append(ChatMessage(isUser: false, text: text, citations: citations))
        case .suggestions(let items): suggestions = items
        case .failure(let text): error = text
        case .done(let outcome):
            isStreaming = false; status = ""; isPending = false
            if outcome != "complete" && error == nil { error = "Подбор прервался. Попробуйте ещё раз." }
        case .pending: isPending = true
        case .provider, .ignored: break
        }
    }
}
