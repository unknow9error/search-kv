import Foundation
import Testing
@testable import MekenCore

@Test func sseHandlesCommentsCRLFAndMultilineUnicode() throws {
    var parser = SSEParser()
    #expect(try parser.consume(line: ": keepalive\r") == nil)
    #expect(try parser.consume(line: "id: 7\r") == nil)
    #expect(try parser.consume(line: "event: message\r") == nil)
    #expect(try parser.consume(line: "data: {\"text\":\"Астана 🏠\",\r") == nil)
    #expect(try parser.consume(line: "data: \"citations\":[]}\r") == nil)
    let parsed = try parser.consume(line: "\r")
    let frame = try #require(parsed)
    #expect(frame.id == 7)
    let event = try ServerEvent.decode(kind: frame.event, data: Data(frame.data.utf8))
    if case .message(let text, let citations) = event { #expect(text == "Астана 🏠"); #expect(citations.isEmpty) }
    else { Issue.record("Incorrect event") }
    #expect(try parser.consume(line: "") == nil)
}

@Test func schemaRoundtripPreservesMoneyAndPreferences() throws {
    var preferences = Preferences(city: "Астана", budgetMax: 35_500_000)
    preferences.requiredAmenities = ["school"]
    let data = try APIJSON.encoder().encode(preferences)
    #expect(String(decoding: data, as: UTF8.self).contains("budget_max"))
    #expect(try APIJSON.decoder().decode(Preferences.self, from: data) == preferences)
}

@Test func frameLimitPreventsUnboundedMemory() throws {
    var parser = SSEParser()
    #expect(throws: StreamError.self) { try parser.consume(line: "data: " + String(repeating: "a", count: 2_000_001)) }
}

@Test func unknownEventAllowsForwardCompatibility() throws {
    var state = SearchState()
    state.apply(try ServerEvent.decode(kind: "future_event", data: Data("{}".utf8)))
    #expect(state.messages.isEmpty)
}

@Test func failureDoesNotErasePartialResultsOrHistory() {
    var state = SearchState()
    state.messages = [ChatMessage(isUser: true, text: "Хочу квартиру")]
    state.isStreaming = true
    state.apply(.failure("Источник пока не отвечает"))
    state.apply(.done("failed"))
    #expect(state.messages.count == 1)
    #expect(state.error == "Источник пока не отвечает")
    #expect(!state.isStreaming)
}

@Test func externalLinksRejectInsecureOrCredentialURLs() {
    #expect(Apartment.httpsURL("http://example.com") == nil)
    #expect(Apartment.httpsURL("javascript:alert(1)") == nil)
    #expect(Apartment.httpsURL("https://user:password@example.com") == nil)
    #expect(Apartment.httpsURL("https://bi.group/ru") != nil)
}

@Test func snakeCaseHistoryPayloadKeepsEventSchema() throws {
    let raw = #"{"sequence":2,"kind":"preferences","payload":{"city":"Астана","budget_max":35000000,"rooms":[],"preferred_amenities":[],"required_amenities":[],"amenity_radius_m":1000}}"#
    let event = try APIJSON.decoder().decode(HistoryEvent.self, from: Data(raw.utf8))
    var state = SearchState()
    state.apply(try event.streamEvent())
    #expect(state.preferences.budgetMax == 35_000_000)
    #expect(state.preferences.city == "Астана")
}

@Test func rawBytesPreserveBlankFramesAndSplitUTF8() throws {
    let input = "\u{feff}id: 1\r\nevent: accepted\r\ndata: {\"turn_id\":\"abc\"}\r\n\r\nevent: message\ndata: {\"text\":\"Квартира 🏠\",\"citations\":[]}\n\nevent: done\rdata: {\"status\":\"complete\"}\r\r"
    var parser = SSEByteParser()
    var frames: [SSEFrame] = []
    for byte in input.utf8 { if let frame = try parser.consume(byte: byte) { frames.append(frame) } }
    #expect(frames.count == 3)
    #expect(frames[0].id == 1)
    #expect(frames[1].data.contains("Квартира 🏠"))
    #expect(frames[2].event == "done")
}
