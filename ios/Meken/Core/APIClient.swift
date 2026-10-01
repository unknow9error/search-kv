import Foundation
import MekenCore

enum APIError: Error, LocalizedError {
    case invalidConfiguration, http(Int, String), invalidResponse, noSession
    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: return "Не настроен адрес сервиса."
        case .noSession: return "Сессия завершилась. Начните новый подбор."
        case .invalidResponse: return "Не удалось прочитать ответ. Попробуйте ещё раз."
        case .http(_, let code):
            switch code {
            case "rate_limited": return "Слишком много запросов. Попробуйте немного позже."
            case "turn_in_progress": return "Текущий подбор ещё идёт. Дождитесь его завершения."
            case "apartment_not_found": return "Это предложение больше не доступно в каталоге."
            case "conversation_not_found": return "Этот диалог больше не доступен. Начните новый."
            case "session_expired": return "Сессия завершилась. Начните новый подбор."
            case "favorites_limit": return "Сохранено уже 200 квартир. Удалите ненужные, чтобы добавить новые."
            case "conversation_limit", "conversations_limit": return "Начните новый подбор или удалите ненужные диалоги."
            default: return "Сервис пока недоступен. Проверьте интернет и повторите попытку."
            }
        }
    }
}

/// Owns network/auth state; no main-actor dependency and no API keys in the app.
actor APIClient {
    let baseURL: URL
    private let session: URLSession
    private let vault: TokenVault
    private var tokens: TokenPair?
    private var refreshTask: Task<TokenPair, Error>?

    init(baseURL: URL) {
        self.baseURL = baseURL
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 40
        configuration.timeoutIntervalForResource = 150
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        self.session = URLSession(configuration: configuration)
        self.vault = TokenVault(service: "kz.meken.auth." + (baseURL.host ?? "unknown") + "." + String(baseURL.port ?? 443))
    }

    func bootstrap() async throws {
        if tokens != nil { return }
        if let saved = try vault.load() { tokens = saved; return }
        let created: TokenPair = try await call("v1/auth/anonymous", method: "POST", authenticated: false)
        try vault.save(created)
        tokens = created
    }

    private func request(_ path: String, method: String, body: Data?, authenticated: Bool) throws -> URLRequest {
        guard !path.hasPrefix("/"), !path.contains(".."), var components = URLComponents(url: baseURL, resolvingAgainstBaseURL: false) else { throw APIError.invalidConfiguration }
        let split = path.split(separator: "?", maxSplits: 1)
        components.path = baseURL.path.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + "/" + String(split[0])
        if !components.path.hasPrefix("/") { components.path = "/" + components.path }
        components.percentEncodedQuery = split.count > 1 ? String(split[1]) : nil
        guard let url = components.url else { throw APIError.invalidConfiguration }
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.setValue("application/json", forHTTPHeaderField: "Content-Type")
        if authenticated {
            guard let tokens else { throw APIError.noSession }
            request.setValue("Bearer " + tokens.accessToken, forHTTPHeaderField: "Authorization")
        }
        return request
    }

    private func refresh() async throws {
        if let refreshTask { tokens = try await refreshTask.value; return }
        guard let old = tokens else { throw APIError.noSession }
        struct Refresh: Encodable { let refreshToken: String }
        let req = try request("v1/auth/refresh", method: "POST", body: APIJSON.encoder().encode(Refresh(refreshToken: old.refreshToken)), authenticated: false)
        let network = session
        let task = Task<TokenPair, Error> {
            let (data, response) = try await network.data(for: req)
            try Self.validate(response, data: data)
            return try APIJSON.decoder().decode(TokenPair.self, from: data)
        }
        refreshTask = task
        defer { refreshTask = nil }
        let refreshed = try await task.value
        try vault.save(refreshed)
        tokens = refreshed
    }

    func call<T: Decodable & Sendable>(_ path: String, method: String = "GET", body: Data? = nil, authenticated: Bool = true) async throws -> T {
        var req = try request(path, method: method, body: body, authenticated: authenticated)
        let accessUsed = tokens?.accessToken
        var (data, response) = try await session.data(for: req)
        if authenticated, (response as? HTTPURLResponse)?.statusCode == 401 {
            if tokens?.accessToken == accessUsed { try await refresh() }
            req = try request(path, method: method, body: body, authenticated: true)
            (data, response) = try await session.data(for: req)
        }
        try Self.validate(response, data: data)
        return try APIJSON.decoder().decode(T.self, from: data.isEmpty ? Data("{}".utf8) : data)
    }

    func perform(_ path: String, method: String) async throws {
        let _: EmptyResponse = try await call(path, method: method)
    }

    private struct EmptyResponse: Decodable, Sendable {}

    func stream(conversationID: String, message: String, selected: [String], clientTurnID: UUID) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        struct Body: Encodable { let clientTurnId: UUID; let message: String; let selectedListingIds: [String] }
        let body = try APIJSON.encoder().encode(Body(clientTurnId: clientTurnID, message: message, selectedListingIds: selected))
        return try await openStream("v1/conversations/\(conversationID)/turns", method: "POST", body: body)
    }

    func replay(conversationID: String, turnID: String, after: Int) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        try await openStream("v1/conversations/\(conversationID)/turns/\(turnID)/events?after=\(after)", method: "GET", body: nil)
    }

    private func openStream(_ path: String, method: String, body: Data?) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        var req = try request(path, method: method, body: body, authenticated: true)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let accessUsed = tokens?.accessToken
        var (bytes, response) = try await session.bytes(for: req)
        if (response as? HTTPURLResponse)?.statusCode == 401 {
            if tokens?.accessToken == accessUsed { try await refresh() }
            req = try request(path, method: method, body: body, authenticated: true)
            req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            (bytes, response) = try await session.bytes(for: req)
        }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard http.statusCode == 200 else { throw APIError.http(http.statusCode, http.statusCode == 409 ? "turn_in_progress" : "temporarily_unavailable") }
        guard http.value(forHTTPHeaderField: "Content-Type")?.contains("text/event-stream") == true else { throw APIError.invalidResponse }
        let streamBytes = bytes
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    var parser = SSEByteParser()
                    for try await byte in streamBytes {
                        try Task.checkCancellation()
                        if let frame = try parser.consume(byte: byte) {
                            let event = try ServerEvent.decode(kind: frame.event, data: Data(frame.data.utf8))
                            continuation.yield((frame.id, event))
                        }
                    }
                    continuation.finish()
                } catch { continuation.finish(throwing: error) }
            }
            continuation.onTermination = { _ in task.cancel() }
        }
    }

    func deleteAccount() async throws {
        try await perform("v1/me", method: "DELETE")
        try vault.clear()
        tokens = nil
    }

    nonisolated static func validate(_ response: URLResponse, data: Data) throws {
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard (200..<300).contains(http.statusCode) else {
            struct Failure: Decodable { struct Body: Decodable { let code: String }; let error: Body }
            let code = (try? APIJSON.decoder().decode(Failure.self, from: data).error.code) ?? "temporarily_unavailable"
            throw APIError.http(http.statusCode, code)
        }
    }
}
