import Foundation

public struct Preferences: Codable, Equatable, Sendable {
    public var city: String?
    public var budgetMax: Int?
    public var rooms: [Int] = []
    public var areaMin: Double?
    public var floorMin: Int?
    public var floorMax: Int?
    public var preferredAmenities: [String] = []
    public var requiredAmenities: [String] = []
    public var amenityRadiusM: Int = 1000
    public var amenityScope: String = "nearby"

    private enum CodingKeys: String, CodingKey {
        case city, budgetMax, rooms, areaMin, floorMin, floorMax, preferredAmenities, requiredAmenities, amenityRadiusM, amenityScope
    }
    public init(from decoder: Decoder) throws {
        let c = try decoder.container(keyedBy: CodingKeys.self)
        city = try c.decodeIfPresent(String.self, forKey: .city)
        budgetMax = try c.decodeIfPresent(Int.self, forKey: .budgetMax)
        rooms = try c.decodeIfPresent([Int].self, forKey: .rooms) ?? []
        areaMin = try c.decodeIfPresent(Double.self, forKey: .areaMin)
        floorMin = try c.decodeIfPresent(Int.self, forKey: .floorMin)
        floorMax = try c.decodeIfPresent(Int.self, forKey: .floorMax)
        preferredAmenities = try c.decodeIfPresent([String].self, forKey: .preferredAmenities) ?? []
        requiredAmenities = try c.decodeIfPresent([String].self, forKey: .requiredAmenities) ?? []
        amenityRadiusM = try c.decodeIfPresent(Int.self, forKey: .amenityRadiusM) ?? 1000
        amenityScope = try c.decodeIfPresent(String.self, forKey: .amenityScope) ?? "nearby"
    }

    public init(city: String? = nil, budgetMax: Int? = nil) {
        self.city = city
        self.budgetMax = budgetMax
    }
}

public struct Apartment: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let providerId: String
    public let providerName: String
    public let complexName: String
    public let city: String
    public let district: String
    public let address: String
    public let rooms: Int
    public let areaM2: Double
    public let floor: Int
    public let totalFloors: Int
    public let priceKzt: Int
    public let status: String
    public let latitude: Double?
    public let longitude: Double?
    public let finish: String
    public let completion: String
    public let sourceUrl: String
    public let imageUrl: String?
    public let observedAt: Date
    public let version: Int
    public let provenance: String
    public let freshness: String
    public let reasons: [String]
    public let tradeoffs: [String]
    public let amenities: [Amenity]
    public let bigvilleName: String?
    public let projectFacts: [ProjectFact]?

    public var isDemo: Bool { provenance == "demo" }
    public var canOpenSource: Bool { provenance == "provider" && safeSourceURL != nil }
    public var safeSourceURL: URL? { Self.httpsURL(sourceUrl) }
    public var safeImageURL: URL? { Self.httpsURL(imageUrl) }
    public var isRecent: Bool { freshness == "recent" && (0...300).contains(Date().timeIntervalSince(observedAt)) }
    public var observedAtLabel: String {
        observedAt.formatted(.dateTime.day().month(.wide).year().hour().minute().locale(Locale(identifier: "ru_KZ")))
    }
    public var priceLabel: String {
        priceKzt.formatted(.number.locale(Locale(identifier: "ru_KZ"))) + " ₸"
    }
    public var areaLabel: String {
        areaM2.formatted(.number.precision(.fractionLength(0...1)).locale(Locale(identifier: "ru_KZ"))) + " м²"
    }
    public var statusLabel: String {
        if isDemo { return "Демонстрационный вариант" }
        switch status {
        case "sold": return "Продана по данным застройщика"
        case "reserved": return "Забронирована"
        case "available": return isRecent ? "В предложениях застройщика" : "Наличие нужно уточнить"
        default: return "Наличие не подтверждено"
        }
    }
    public static func httpsURL(_ raw: String?) -> URL? {
        guard let raw, let url = URL(string: raw), url.scheme == "https", url.host != nil,
              url.user == nil, url.password == nil else { return nil }
        return url
    }
}

public struct Amenity: Codable, Identifiable, Equatable, Sendable {
    public let kind: String
    public let name: String
    public let distanceM: Int
    public let sourceUrl: String
    public let observedAt: Date
    public let distanceType: String
    public var id: String { kind + ":" + name + ":" + sourceUrl }
    public var symbol: String {
        switch kind {
        case "school": return "graduationcap"
        case "kindergarten": return "figure.and.child.holdinghands"
        case "park": return "leaf"
        default: return "tram"
        }
    }
}

public struct Citation: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let title: String
    public let url: String
    public let demo: Bool
    public var safeURL: URL? { demo ? nil : Apartment.httpsURL(url) }
}

public struct AppConfig: Codable, Sendable {
    public let mode: String
    public let aiEnabled: Bool
    public let cities: [String]
    public let privacyUrl: String?
    public let termsUrl: String?
    public let retentionDays: Int
    public var isDemo: Bool { mode == "demo" }
}

public struct TokenPair: Codable, Sendable {
    public let accessToken: String
    public let refreshToken: String
    public let expiresIn: Int
    public let userId: String
}

public struct ConversationSummary: Codable, Identifiable, Sendable {
    public let id: String
    public let title: String
    public let preferences: Preferences
}

public struct ConversationHistory: Codable, Sendable {
    public let id: String
    public let title: String
    public let preferences: Preferences
    public let turns: [HistoryTurn]
}

public struct HistoryTurn: Codable, Identifiable, Sendable {
    public let id: String
    public let message: String
    public let status: String
    public let events: [HistoryEvent]
}

public struct HistoryEvent: Codable, Sendable {
    public let sequence: Int
    public let kind: String
    public let payload: JSONValue
    public func streamEvent() throws -> ServerEvent {
        try ServerEvent.decode(kind: kind, data: APIJSON.encoder().encode(payload))
    }
}

public struct Items<T: Codable & Sendable>: Codable, Sendable {
    public let items: [T]
}

public struct Verification: Codable, Sendable {
    public let listing: Apartment?
    public let verification: String
    public let checkedAt: Date
}

public enum JSONValue: Codable, Sendable {
    case object([String: JSONValue]), array([JSONValue]), string(String), number(Double), bool(Bool), null
    public init(from decoder: Decoder) throws {
        let container = try decoder.singleValueContainer()
        if container.decodeNil() { self = .null }
        else if let x = try? container.decode(Bool.self) { self = .bool(x) }
        else if let x = try? container.decode(Double.self) { self = .number(x) }
        else if let x = try? container.decode(String.self) { self = .string(x) }
        else if let x = try? container.decode([JSONValue].self) { self = .array(x) }
        else { self = .object(try container.decode([String: JSONValue].self)) }
    }
    public func encode(to encoder: Encoder) throws {
        var container = encoder.singleValueContainer()
        switch self {
        case .object(let x): try container.encode(x)
        case .array(let x): try container.encode(x)
        case .string(let x): try container.encode(x)
        case .number(let x): try container.encode(x)
        case .bool(let x): try container.encode(x)
        case .null: try container.encodeNil()
        }
    }
}

public enum APIJSON {
    public static func decoder() -> JSONDecoder {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        decoder.dateDecodingStrategy = .custom { value in
            let raw = try value.singleValueContainer().decode(String.self)
            let fractional = ISO8601DateFormatter()
            fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
            if let date = fractional.date(from: raw) { return date }
            let standard = ISO8601DateFormatter()
            if let date = standard.date(from: raw) { return date }
            throw DecodingError.dataCorruptedError(in: try value.singleValueContainer(), debugDescription: "Invalid ISO-8601 date")
        }
        return decoder
    }
    public static func encoder() -> JSONEncoder {
        let encoder = JSONEncoder()
        encoder.keyEncodingStrategy = .convertToSnakeCase
        encoder.dateEncodingStrategy = .iso8601
        return encoder
    }
}


public struct ProjectFact: Codable, Identifiable, Equatable, Sendable {
    public let id: String
    public let kind: String
    public let name: String
    public let state: String
    public let scopeType: String
    public let relation: String?
    public let expectedOpening: String?
    public let evidence: String
    public let sourceUrl: String
    public let observedAt: Date
    public var scopeLabel: String {
        if relation == "within" { return scopeType == "bigville" ? "В бигвилле" : "В ЖК" }
        if relation == "nearby" { return scopeType == "bigville" ? "Рядом с бигвиллем" : "Рядом с ЖК" }
        return scopeType == "bigville" ? "В описании бигвилля" : "В описании ЖК"
    }
    public var statusLabel: String {
        switch state {
        case "operating": return "Действует по данным застройщика"
        case "planned": return "Запланировано"
        case "under_construction": return "Строится"
        default: return "Статус уточняется"
        }
    }
}
