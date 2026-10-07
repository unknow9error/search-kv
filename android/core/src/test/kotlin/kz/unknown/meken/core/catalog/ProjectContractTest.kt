package kz.unknown.meken.core.catalog

import kotlinx.serialization.json.*
import kz.unknown.meken.core.API_JSON
import org.junit.Assert.*
import org.junit.Test

class ProjectContractTest {
    private fun fixture(name: String = "project-page.json"): String = requireNotNull(javaClass.getResourceAsStream("/"+name)).bufferedReader().use { it.readText() }
    @Test fun actualApiResponseRetainsProjectAndLayoutRecordLevels() {
        val page = API_JSON.decodeFromString<ProjectPage>(fixture())
        assertEquals(12, page.total)
        assertEquals("ЖК Северный сад", page.items.first().name)
        assertEquals("demo", page.items.first().provenance)
        assertEquals("published_starting_price", page.items.first().publishedStartingPrice?.kind)
        assertEquals(1,page.items.first().availableLayoutCount)
        assertEquals("layout_type",API_JSON.decodeFromString<CatalogProject>(fixture("project-detail.json")).layouts.first().kind)
        assertNull(page.items.first().observedListingMinimum)
    }
    @Test fun observedMinimumDoesNotFillPublishedStartingPrice() {
        val first = API_JSON.parseToJsonElement(fixture()).jsonObject.getValue("items").jsonArray.first().jsonObject
        val changed = JsonObject(first + mapOf("published_starting_price" to JsonNull,"display_price" to buildJsonObject {
            put("kind","observed_listing_minimum");put("amount_kzt",20_000_000);put("source_url","https://example.org/lots/1");put("observed_at","2026-10-04T08:00:00Z")
        }))
        val value = API_JSON.decodeFromJsonElement<CatalogProject>(changed)
        assertNull(value.publishedStartingPrice)
        assertEquals("observed_listing_minimum",value.displayPrice.kind)
    }
    @Test fun projectPriceCeilingSupportsTenBillionAndAvoidsLegacyBudgetField() {
        val value = ProjectCriteria(city="Астана",priceMax=10_000_000_000L,districts=listOf("Есиль"))
        val json = API_JSON.encodeToJsonElement(value).jsonObject
        assertEquals(10_000_000_000L,json.getValue("price_max").jsonPrimitive.long)
        assertFalse(json.containsKey("budget_max"))
    }
    @Test fun layoutDoesNotInventLotPriceOrFloor() {
        val plan = API_JSON.decodeFromString<CatalogProject>(fixture("project-detail.json")).layouts.first()
        val json = API_JSON.encodeToJsonElement(plan).jsonObject
        assertFalse(json.containsKey("price_kzt"));assertFalse(json.containsKey("floor"))
    }
}
