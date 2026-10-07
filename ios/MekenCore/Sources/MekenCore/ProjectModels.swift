import Foundation

public struct ProjectBounds: Codable, Equatable, Sendable {
    public var south, west, north, east: Double
    public init(south: Double, west: Double, north: Double, east: Double) { self.south = south; self.west = west; self.north = north; self.east = east }
}
public struct ProjectCriteria: Codable, Equatable, Sendable {
    public var city: String?; public var q: String?
    public var districts: [String] = []; public var developerNames: [String] = []; public var providerIds: [String] = []
    public var priceMode = "published_starting_price"; public var priceMin: Int?; public var priceMax: Int?
    public var stages: [String] = []; public var completionBefore: String?; public var rooms: [Int] = []
    public var areaMin: Double?; public var floorMin: Int?; public var floorMax: Int?
    public var requiredAmenities: [String] = []; public var amenityScope = "nearby"; public var amenityRadiusM = 1000
    public var includeStale = false; public var bounds: ProjectBounds?
    public init(city: String? = nil) { self.city = city }
    public var priceModeLabel: String { priceMode == "published_starting_price" ? "Цена ЖК «от»" : "Минимум по опубликованным квартирам" }
    public var priceSummary: String { priceMax.map { "До \(Self.millions($0)) млн ₸" } ?? "Не выбрана" }
    public static func millions(_ value: Int) -> String { (Double(value) / 1_000_000).formatted(.number.locale(Locale(identifier: "ru_KZ")).precision(.fractionLength(0...2))) }
}
public struct ProjectPrice: Codable, Equatable, Sendable {
    public let kind: String; public let amountKzt: Int?; public let sourceUrl: String?; public let observedAt: String?
    public var label: String {
        guard let amountKzt, kind != "unknown" else { return "Цена не опубликована" }
        return "\(kind == "published_starting_price" ? "от " : "Лоты от ")\(ProjectCriteria.millions(amountKzt)) млн ₸"
    }
}
public struct ProjectImage: Codable, Equatable, Sendable { public let url: String; public let kind: String; public let caption: String?; public let sourceUrl: String; public let observedAt: String }
public struct ProjectBuilding: Codable, Equatable, Sendable, Identifiable {
    public let externalId: String; public let name: String; public let stage: String?; public let completion: String?; public let completionDate: String?; public let totalFloors: Int?; public let sourceUrl: String; public let observedAt: String
    public var id: String { externalId }
}
public struct ProjectDocument: Codable, Equatable, Sendable, Identifiable { public let name: String; public let url: String; public let kind: String; public let sourceUrl: String; public let observedAt: String; public var id: String { url } }
public struct ProjectLayout: Codable, Equatable, Sendable, Identifiable {
    public let id: String; public let projectId: String; public let externalId: String; public let name: String; public let rooms: Int?; public let areaM2: Double?; public let imageUrl: String?; public let sourceUrl: String; public let observedAt: String; public let receivedAt: String; public let provenance: String; public let freshness: String; public let kind: String
    public var parameterLabel: String { "\(rooms.map { "\($0) комнаты" } ?? "Комнаты не опубликованы") · \(areaM2.map { $0.formatted(.number.locale(Locale(identifier: "ru_KZ")).precision(.fractionLength(0...1))) + " м²" } ?? "Площадь не опубликована")" }
}
public struct CatalogProject: Codable, Equatable, Sendable, Identifiable {
    public let id: String; public let version: Int?; public let providerId: String; public let providerName: String; public let externalId: String; public let name: String; public let city: String; public let district: String; public let address: String?; public let developerName: String?; public let latitude: Double?; public let longitude: Double?; public let sourceUrl: String; public let websiteUrl: String?; public let websiteScope: String?; public let observedAt: String; public let receivedAt: String; public let provenance: String; public let freshness: String; public let recordOrigin: String
    public let stage: String?; public let completion: String?; public let finish: String?; public let publishedStartingPrice: ProjectPrice?; public let observedListingMinimum: ProjectPrice?; public let displayPrice: ProjectPrice
    public var buildings: [ProjectBuilding]; public let images: [ProjectImage]; public let documents: [ProjectDocument]; public let layouts: [ProjectLayout]; public let publishedLotCount: Int; public let matchedLotCount: Int; public let availableLayoutCount: Int
    public var isDemo: Bool { provenance == "demo" }
    public var stageLabel: String { switch stage { case "commissioned": "Сдан"; case "under_construction": "Строится"; case "planned": "Планируется"; default: "Стадия не опубликована" } }
    public var sourceLabel: String { "\(isDemo ? "Демо · " : "")\(providerName) · \(Self.dateLabel(observedAt))" }
    public static func dateLabel(_ value: String) -> String { let p = value.prefix(10).split(separator: "-"); return p.count == 3 ? "\(p[2]).\(p[1]).\(p[0])" : "Дата не указана" }
    public var sourceURL: URL? { isDemo ? nil : Apartment.httpsURL(websiteUrl ?? sourceUrl) }
}
public struct ProjectSearchBody: Codable, Sendable {
    public let criteria: ProjectCriteria; public var limit = 20; public var cursor: String?; public var sort = "price_asc"
    public init(criteria: ProjectCriteria, cursor: String? = nil, sort: String = "price_asc") { self.criteria = criteria; self.cursor = cursor; self.sort = sort }
}
public struct ProjectPage: Codable, Sendable { public let items: [CatalogProject]; public let total: Int; public let nextCursor: String?; public let criteria: ProjectCriteria; public let sort: String; public let unknownCoordinatesCount: Int }
public struct ProjectFacet: Codable, Sendable, Identifiable { public let value: String; public let count: Int; public var id: String { value } }
public struct ProjectFacets: Codable, Sendable { public let cities: [ProjectFacet]; public let districts: [ProjectFacet]; public let developerNames: [ProjectFacet]; public let stages: [ProjectFacet]; public let priceModes: [ProjectFacet] }
public enum ProjectScalar: Codable, Sendable, Equatable {
    case text(String), number(Double), none
    public init(from decoder: Decoder) throws { let c = try decoder.singleValueContainer(); if c.decodeNil() { self = .none } else if let n = try? c.decode(Double.self) { self = .number(n) } else { self = .text(try c.decode(String.self)) } }
    public func encode(to encoder: Encoder) throws { var c = encoder.singleValueContainer(); switch self { case .text(let v): try c.encode(v); case .number(let v): try c.encode(v); case .none: try c.encodeNil() } }
    public var label: String { switch self { case .text(let s): s; case .number(let n): n.formatted(.number.locale(Locale(identifier: "ru_KZ"))); case .none: "Не опубликовано" } }
}
public struct ProjectComparisonValue: Codable, Sendable { public let projectId: String; public let value: ProjectScalar?; public let sourceUrl: String?; public let observedAt: String? }
public struct ProjectComparisonRow: Codable, Sendable, Identifiable { public let key: String; public let label: String; public let values: [ProjectComparisonValue]; public var id: String { key } }
public struct ProjectComparison: Codable, Sendable { public let projects: [CatalogProject]; public let rows: [ProjectComparisonRow]; public init(projects: [CatalogProject], rows: [ProjectComparisonRow]) { self.projects = projects; self.rows = rows } }
public struct ProjectConversation: Codable, Sendable, Identifiable { public let id: String; public let title: String; public let criteria: ProjectCriteria; public let createdAt: String; public let updatedAt: String }
public struct ProjectCitation: Codable, Sendable { public let projectId: String; public let title: String; public let url: String; public let demo: Bool; public let observedAt: String }
public struct ProjectTurn: Codable, Sendable { public let conversationId: String; public let turnId: String; public let message: String; public let criteria: ProjectCriteria; public let results: ProjectPage?; public let comparison: ProjectComparison?; public let citations: [ProjectCitation]; public let suggestions: [String]; public let unsupportedConditions: [String] }
public struct ProjectHistoryTurn: Codable, Sendable, Identifiable { public let id: String; public let clientTurnId: String; public let message: String; public let state: String; public let createdAt: String; public let response: ProjectTurn? }
public struct ProjectHistory: Codable, Sendable { public let id: String; public let title: String; public let criteria: ProjectCriteria; public let turns: [ProjectHistoryTurn]; public let hasMore: Bool; public let nextBefore: String? }
public struct CatalogSource: Codable, Sendable, Identifiable { public let providerId: String; public let name: String; public let sourceUrl: String?; public let websiteUrl: String?; public let provenance: String; public let projectCount: Int; public let cities: [String]; public var id: String { providerId } }
