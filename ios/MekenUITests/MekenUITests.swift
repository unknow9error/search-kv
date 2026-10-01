import XCTest

@MainActor
final class MekenUITests: XCTestCase {
    func testNativeSearchDetailsAndFavorites() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launch()
        let fresh = app.buttons["Новый подбор"]
        XCTAssertTrue(fresh.waitForExistence(timeout: 30))
        fresh.tap()
        let welcome = XCTAttachment(screenshot: app.screenshot())
        welcome.name = "Welcome"
        welcome.lifetime = .keepAlways
        add(welcome)
        let begin = app.buttons["Астана до 35 млн ₸"]
        if !begin.isHittable { app.swipeUp() }
        XCTAssertTrue(begin.waitForExistence(timeout: 5))
        begin.tap()
        let open = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "apartment-open-")).firstMatch
        XCTAssertTrue(open.waitForExistence(timeout: 45), "Streaming search should display a real or explicitly demo card")
        let stopped = NSPredicate(format: "exists == false")
        expectation(for: stopped, evaluatedWith: app.buttons["Остановить подбор"])
        waitForExpectations(timeout: 45)
        // Move the first card's text button into view before interacting.
        app.swipeUp()
        let candidate = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "apartment-open-")).allElementsBoundByIndex.first { element in
            let frame = element.frame
            return !frame.isInfinite && !frame.isNull && frame.minY > 120 && frame.maxY < 725 && frame.height > 20
        }
        XCTAssertNotNil(candidate)
        try XCTUnwrap(candidate).tap()
        let detailFavorite = app.buttons["detail-favorite"]
        XCTAssertTrue(detailFavorite.waitForExistence(timeout: 5))
        if detailFavorite.label == "Сохранить квартиру" { detailFavorite.tap() }
        let savedPredicate = NSPredicate(format: "label == %@", "Убрать из избранного")
        expectation(for: savedPredicate, evaluatedWith: detailFavorite)
        waitForExpectations(timeout: 10)
        app.buttons["Готово"].tap()
        app.tabBars.buttons["Избранное"].tap()
        let saved = app.buttons.matching(NSPredicate(format: "identifier BEGINSWITH %@", "apartment-open-")).firstMatch
        XCTAssertTrue(saved.waitForExistence(timeout: 10))
        saved.tap()
        XCTAssertTrue(app.navigationBars["Квартира"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.staticTexts["Инфраструктура проекта"].exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Apartment detail"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        app.buttons["Готово"].tap()
    }
}
