import XCTest

@MainActor final class MekenUITests: XCTestCase {
    func testAllThirteenCatalogPages() throws {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--catalog-onboarding"]
        app.launch()
        func shot(_ name: String) { let attachment = XCTAttachment(screenshot: app.screenshot()); attachment.name = name; attachment.lifetime = .keepAlways; add(attachment) }
        func tap(_ title: String) { let button = app.buttons[title].firstMatch; XCTAssertTrue(button.waitForExistence(timeout: 20), title); button.tap() }
        func back() { tap("Назад") }
        let begin = app.buttons["Начать поиск"]
        XCTAssertTrue(begin.waitForExistence(timeout: 20))
        expectation(for: NSPredicate(format: "enabled == true"), evaluatedWith: begin)
        waitForExpectations(timeout: 20)
        shot("01-onboarding"); begin.tap()
        XCTAssertTrue(app.staticTexts["Поиск ЖК"].waitForExistence(timeout: 5))
        shot("02-search")
        tap("catalog-filters")
        XCTAssertTrue(app.staticTexts["Фильтры"].waitForExistence(timeout: 5))
        app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "Район")).firstMatch.tap()
        tap("Есиль")
        let price = app.textFields["Не выбрана"]
        price.tap(); price.typeText("35")
        app.swipeDown()
        shot("03-filters")
        tap("Показать ЖК")
        XCTAssertTrue(app.staticTexts["12 ЖК"].waitForExistence(timeout: 20))
        shot("04-results")
        tap("Карта")
        XCTAssertTrue(app.buttons["Открыть ЖК"].waitForExistence(timeout: 10))
        shot("05-map")
        tap("Открыть ЖК")
        XCTAssertTrue(app.buttons["Планировки"].waitForExistence(timeout: 10))
        shot("07-project")
        tap("Сохранить ЖК")
        tap("Планировки")
        let plan = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "2 комнаты")).firstMatch
        XCTAssertTrue(plan.waitForExistence(timeout: 10)); plan.tap()
        XCTAssertTrue(app.staticTexts["Планировка"].waitForExistence(timeout: 5))
        shot("08-layout"); back(); back()
        tap("Список")
        let compares = app.buttons["Сравнить"]
        for _ in 0..<6 { if compares.firstMatch.isHittable { break }; app.swipeUp() }
        compares.firstMatch.tap()
        app.swipeUp()
        let second = app.buttons["Сравнить"].firstMatch
        XCTAssertTrue(second.waitForExistence(timeout: 5)); second.tap()
        tap("Сравнить 2 ЖК")
        XCTAssertTrue(app.staticTexts["Сравнение ЖК"].waitForExistence(timeout: 10))
        shot("09-comparison"); back()
        tap("catalog-tab-1")
        XCTAssertTrue(app.staticTexts["Избранное"].firstMatch.waitForExistence(timeout: 5))
        shot("10-favorites")
        tap("catalog-tab-0")
        // Results remain on the search tab; start a fresh project conversation from profile history.
        tap("catalog-tab-2")
        XCTAssertTrue(app.staticTexts["Профиль"].firstMatch.waitForExistence(timeout: 5))
        shot("12-profile")
        tap("История поиска")
        shot("11-history")
        tap("Новый поиск")
        tap("Уточнить в разговоре")
        let message = app.textFields["Уточнить пожелания"]
        XCTAssertTrue(message.waitForExistence(timeout: 5)); message.tap(); message.typeText("Ищу ЖК в Есиле. Хочу понять сроки и цены.")
        tap("Отправить")
        let sent = NSPredicate(format: "exists == true")
        expectation(for: sent, evaluatedWith: app.staticTexts["Ищу ЖК в Есиле. Хочу понять сроки и цены."])
        waitForExpectations(timeout: 20)
        app.swipeDown()
        shot("06-conversation"); back()
        tap("catalog-tab-2")
        let help = app.buttons["Помощь и документы"]
        if !help.isHittable { app.swipeUp() }
        help.tap()
        XCTAssertTrue(app.staticTexts["Помощь и документы"].waitForExistence(timeout: 5))
        shot("13-help")
        tap("Источники каталога")
        XCTAssertTrue(app.staticTexts["Источники каталога"].waitForExistence(timeout: 5))
        back()
    }
}
