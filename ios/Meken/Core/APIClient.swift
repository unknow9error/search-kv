import Foundation
import MekenCore

enum APIError: Error, LocalizedError {
    case invalidConfiguration, http(Int, String), invalidResponse, noSession, localCleanupAfterDeletion, localCleanupAfterRecovery
    var requiresSessionReset: Bool { if case .http(401, "session_expired") = self { return true }; return false }
    var errorDescription: String? {
        switch self {
        case .invalidConfiguration: return "Не настроен адрес сервиса."
        case .noSession: return "Сессия завершилась. Начните новый подбор."
        case .localCleanupAfterDeletion: return "Данные удалены с сервера. Не удалось полностью удалить локальные данные. Разблокируйте устройство и начните новую сессию."
        case .localCleanupAfterRecovery: return "Доступ к подборкам восстановлен. Не удалось очистить прежние локальные данные. Повторите подключение к сервису."
        case .invalidResponse: return "Не удалось прочитать ответ. Попробуйте ещё раз."
        case .http(_, let code):
            switch code {
            case "rate_limited": return "Слишком много запросов. Попробуйте немного позже."
            case "turn_in_progress": return "Текущий подбор ещё идёт. Дождитесь его завершения."
            case "idempotency_conflict": return "Этот запрос уже сохранён с другими условиями. Начните новый подбор."
            case "project_not_found": return "Этот ЖК больше не доступен в каталоге."
            case "layout_not_found": return "Эта планировка больше не доступна в каталоге."
            case "apartment_not_found": return "Это предложение больше не доступно в каталоге."
            case "conversation_not_found": return "Этот диалог больше не доступен. Начните новый."
            case "session_expired": return "Сессия завершилась. Начните новый подбор."
            case "invalid_recovery_code": return "Код восстановления не подошёл. Проверьте его или используйте новый код с прежнего устройства."
            case "account_recovery_unavailable": return "Восстановление профиля пока недоступно. Попробуйте позднее."
            case "favorites_limit": return "Сохранено уже 200 объектов. Удалите ненужные, чтобы добавить новые."
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
    private let legacyVault: TokenVault?
    private let pendingVault: TokenVault
    private let pendingConversationVault: TokenVault
    private var tokens: TokenPair?
    private var refreshTask: Task<TokenPair, Error>?
    private var identityGeneration = UUID()
    private struct PendingEnvelope: Codable { let userId: String; let request: PendingTurnRequest }
    private struct PendingConversationEnvelope: Codable { let userId: String; let request: PendingConversationRequest }

    init(baseURL: URL) {
        self.baseURL = baseURL
        let configuration = URLSessionConfiguration.ephemeral
        configuration.timeoutIntervalForRequest = 40
        configuration.timeoutIntervalForResource = 150
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        self.session = URLSession(configuration: configuration)
        let service = "kz.meken.auth." + baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/"))
        self.vault = TokenVault(service: service)
        self.pendingVault = TokenVault(service: service, account: "pending-turn")
        self.pendingConversationVault = TokenVault(service: service, account: "pending-conversation")
        // Preserve existing production sessions while moving to endpoint-scoped storage.
        self.legacyVault = baseURL.scheme == "https" && (baseURL.path.isEmpty || baseURL.path == "/")
            ? TokenVault(service: "kz.meken.auth." + (baseURL.host ?? "unknown") + "." + String(baseURL.port ?? 443)) : nil
    }

    func bootstrap() async throws {
        if tokens != nil { return }
        if let saved = try vault.load() { tokens = saved; return }
        if let saved = try legacyVault?.load() {
            try vault.save(saved)
            try legacyVault?.clear()
            tokens = saved
            return
        }
        let generation = identityGeneration
        let created: TokenPair = try await call("v1/auth/anonymous", method: "POST", authenticated: false)
        guard generation == identityGeneration else { throw CancellationError() }
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
        let generation = identityGeneration
        if let refreshTask {
            let value = try await refreshTask.value
            guard generation == identityGeneration else { throw CancellationError() }
            tokens = value
            return
        }
        guard let old = tokens else { throw APIError.noSession }
        struct Refresh: Encodable { let refreshToken: String }
        let req = try request("v1/auth/refresh", method: "POST", body: APIJSON.encoder().encode(Refresh(refreshToken: old.refreshToken)), authenticated: false)
        let network = session
        let task = Task<TokenPair, Error> {
            let (data, response) = try await network.data(for: req)
            try Self.validate(response, data: data)
            let refreshed = try APIJSON.decoder().decode(TokenPair.self, from: data)
            return try self.persistRefresh(refreshed, generation: generation)
        }
        refreshTask = task
        defer { if generation == identityGeneration { refreshTask = nil } }
        let refreshed = try await task.value
        guard generation == identityGeneration else { throw CancellationError() }
        tokens = refreshed
    }

    private func persistRefresh(_ refreshed: TokenPair, generation: UUID) throws -> TokenPair {
        guard generation == identityGeneration else { throw CancellationError() }
        try vault.save(refreshed)
        tokens = refreshed
        return refreshed
    }

    func call<T: Decodable & Sendable>(_ path: String, method: String = "GET", body: Data? = nil, authenticated: Bool = true) async throws -> T {
        let generation = identityGeneration
        var req = try request(path, method: method, body: body, authenticated: authenticated)
        let accessUsed = tokens?.accessToken
        var (data, response) = try await session.data(for: req)
        guard generation == identityGeneration else { throw CancellationError() }
        if authenticated, (response as? HTTPURLResponse)?.statusCode == 401 {
            if tokens?.accessToken == accessUsed { try await refresh() }
            req = try request(path, method: method, body: body, authenticated: true)
            (data, response) = try await session.data(for: req)
        }
        guard generation == identityGeneration else { throw CancellationError() }
        try Self.validate(response, data: data)
        return try APIJSON.decoder().decode(T.self, from: data.isEmpty ? Data("{}".utf8) : data)
    }

    func perform(_ path: String, method: String) async throws {
        let _: EmptyResponse = try await call(path, method: method)
    }

    private struct EmptyResponse: Decodable, Sendable {}

    func stream(conversationID: String, message: String, selected: [String], clientTurnID: UUID) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        try await stream(request: PendingTurnRequest(clientTurnId: clientTurnID, conversationId: conversationID, message: message, selectedListingIds: selected))
    }

    func stream(request: PendingTurnRequest) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        try await openStream("v1/conversations/\(request.conversationId)/turns", method: "POST", body: request.body())
    }

    func userID() -> String? { tokens?.userId }
    func cacheOwnerKey() -> String? {
        tokens.map { baseURL.absoluteString.trimmingCharacters(in: CharacterSet(charactersIn: "/")) + ":" + $0.userId }
    }
    func savePending(_ request: PendingTurnRequest) throws {
        guard let tokens else { throw APIError.noSession }
        try pendingVault.saveValue(PendingEnvelope(userId: tokens.userId, request: request))
    }
    func loadPending() throws -> PendingTurnRequest? {
        guard let saved = try pendingVault.loadValue(PendingEnvelope.self) else { return nil }
        guard saved.userId == tokens?.userId else { try pendingVault.clear(); return nil }
        return saved.request
    }
    func clearPending(ifMatching id: UUID) throws {
        if let saved = try pendingVault.loadValue(PendingEnvelope.self), saved.request.clientTurnId == id { try pendingVault.clear() }
    }

    func savePendingConversation(_ request: PendingConversationRequest) throws {
        guard let tokens else { throw APIError.noSession }
        try pendingConversationVault.saveValue(PendingConversationEnvelope(userId: tokens.userId, request: request))
    }
    func loadPendingConversation() throws -> PendingConversationRequest? {
        guard let saved = try pendingConversationVault.loadValue(PendingConversationEnvelope.self) else { return nil }
        guard saved.userId == tokens?.userId else { try pendingConversationVault.clear(); return nil }
        return saved.request
    }
    func clearPendingConversation(ifMatching id: UUID) throws {
        if let saved = try pendingConversationVault.loadValue(PendingConversationEnvelope.self), saved.request.clientConversationId == id {
            try pendingConversationVault.clear()
        }
    }

    func resetSession() throws {
        identityGeneration = UUID()
        refreshTask?.cancel()
        refreshTask = nil
        tokens = nil
        try vault.clear()
        try legacyVault?.clear()
        try pendingVault.clear()
        try pendingConversationVault.clear()
    }

    func generateRecoveryCode() async throws -> String {
        struct RecoveryCode: Decodable, Sendable { let recoveryCode: String }
        let response: RecoveryCode = try await call("v1/auth/recovery-code", method: "POST")
        return response.recoveryCode
    }

    func recoverAccount(code: String) async throws {
        struct Body: Encodable { let recoveryCode: String }
        let generation = identityGeneration
        let recovered: TokenPair
        do { recovered = try await call("v1/auth/recover", method: "POST", body: APIJSON.encoder().encode(Body(recoveryCode: code)), authenticated: false) }
        catch APIError.http(404, _) { throw APIError.http(404, "account_recovery_unavailable") }
        guard generation == identityGeneration else { throw CancellationError() }
        // A rejected code or failed atomic Keychain update leaves the current profile intact.
        try vault.save(recovered)
        identityGeneration = UUID()
        refreshTask?.cancel()
        refreshTask = nil
        tokens = recovered
        try? legacyVault?.clear()
        try? pendingVault.clear()
        try? pendingConversationVault.clear()
    }

    func replay(conversationID: String, turnID: String, after: Int) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        try await openStream("v1/conversations/\(conversationID)/turns/\(turnID)/events?after=\(after)", method: "GET", body: nil)
    }

    private func openStream(_ path: String, method: String, body: Data?) async throws -> AsyncThrowingStream<(Int?, ServerEvent), Error> {
        let generation = identityGeneration
        var req = try request(path, method: method, body: body, authenticated: true)
        req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
        let accessUsed = tokens?.accessToken
        var (bytes, response) = try await session.bytes(for: req)
        guard generation == identityGeneration else { throw CancellationError() }
        if (response as? HTTPURLResponse)?.statusCode == 401 {
            if tokens?.accessToken == accessUsed { try await refresh() }
            req = try request(path, method: method, body: body, authenticated: true)
            req.setValue("text/event-stream", forHTTPHeaderField: "Accept")
            (bytes, response) = try await session.bytes(for: req)
        }
        guard generation == identityGeneration else { throw CancellationError() }
        guard let http = response as? HTTPURLResponse else { throw APIError.invalidResponse }
        guard http.statusCode == 200 else {
            var errorData = Data()
            for try await byte in bytes { if errorData.count >= 64_000 { break }; errorData.append(byte) }
            try Self.validate(response, data: errorData)
            throw APIError.invalidResponse
        }
        guard http.value(forHTTPHeaderField: "Content-Type")?.contains("text/event-stream") == true else { throw APIError.invalidResponse }
        let streamBytes = bytes
        let headerTurnID = http.value(forHTTPHeaderField: "X-Turn-ID")
        return AsyncThrowingStream { continuation in
            let task = Task {
                do {
                    if let headerTurnID { continuation.yield((nil, .accepted(turnID: headerTurnID))) }
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
        do { try resetSession() }
        catch { throw APIError.localCleanupAfterDeletion }
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
