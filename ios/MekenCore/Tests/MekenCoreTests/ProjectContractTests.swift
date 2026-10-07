import XCTest
@testable import MekenCore

final class ProjectContractTests: XCTestCase {
    private func fixture() throws -> Data {
        try Data(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("Fixtures/project-page.json"))
    }
    func testActualProjectApiResponseDecodesAndPreservesRecordLevels() throws {
        let page = try APIJSON.decoder().decode(ProjectPage.self, from: fixture())
        XCTAssertEqual(page.total, 12)
        let first = try XCTUnwrap(page.items.first)
        XCTAssertEqual(first.name, "ЖК Северный сад")
        XCTAssertEqual(first.provenance, "demo")
        XCTAssertEqual(first.publishedStartingPrice?.kind, "published_starting_price")
        XCTAssertEqual(first.availableLayoutCount, 1)
        let detailData = try Data(contentsOf: URL(fileURLWithPath: #filePath).deletingLastPathComponent().appendingPathComponent("Fixtures/project-detail.json"))
        let detail = try APIJSON.decoder().decode(CatalogProject.self, from: detailData)
        XCTAssertEqual(detail.layouts.first?.kind, "layout_type")
        XCTAssertNil(first.observedListingMinimum)
    }
    func testObservedLotMinimumDoesNotBecomePublishedStartingPrice() throws {
        let page = try JSONSerialization.jsonObject(with: fixture()) as! [String: Any]
        var project = (page["items"] as! [[String: Any]])[0]
        project["published_starting_price"] = NSNull()
        project["display_price"] = ["kind": "observed_listing_minimum", "amount_kzt": 20_000_000, "source_url": "https://example.org/lots/1", "observed_at": "2026-10-04T08:00:00Z"]
        let value = try APIJSON.decoder().decode(CatalogProject.self, from: JSONSerialization.data(withJSONObject: project))
        XCTAssertNil(value.publishedStartingPrice)
        XCTAssertEqual(value.displayPrice.label, "Лоты от 20 млн ₸")
    }
    func testCriteriaUseProjectContractAndSupportTenBillionTenge() throws {
        var criteria = ProjectCriteria(city: "Астана")
        criteria.priceMax = 10_000_000_000
        criteria.districts = ["Есиль"]
        let json = try JSONSerialization.jsonObject(with: APIJSON.encoder().encode(criteria)) as! [String: Any]
        XCTAssertEqual(json["price_max"] as? Int, 10_000_000_000)
        XCTAssertEqual(json["price_mode"] as? String, "published_starting_price")
        XCTAssertNil(json["budget_max"])
    }
    func testUnknownPriceRemainsUnknown() throws {
        let value = try APIJSON.decoder().decode(ProjectPrice.self, from: Data(#"{"kind":"unknown","amount_kzt":null,"source_url":null,"observed_at":null}"#.utf8))
        XCTAssertEqual(value.label, "Цена не опубликована")
    }
}
