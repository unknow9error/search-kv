package kz.unknown.meken.core.catalog
import kotlinx.serialization.Serializable
import kotlinx.serialization.SerialName
import kotlinx.serialization.json.JsonElement

@Serializable
data class CatalogSource(
    @SerialName("provider_id") val providerId: String,
    @SerialName("name") val name: String,
    @SerialName("source_url") val sourceUrl: String? = null,
    @SerialName("website_url") val websiteUrl: String? = null,
    @SerialName("website_scope") val websiteScope: String? = null,
    @SerialName("provenance") val provenance: String,
    @SerialName("project_count") val projectCount: Int,
    @SerialName("public_project_count") val publicProjectCount: Int,
    @SerialName("observed_lot_project_count") val observedLotProjectCount: Int,
    @SerialName("apartment_count") val apartmentCount: Int,
    @SerialName("cities") val cities: List<String> = emptyList(),
    @SerialName("last_snapshot_at") val lastSnapshotAt: String? = null,
    @SerialName("last_success_at") val lastSuccessAt: String? = null,
    @SerialName("latest_observed_at") val latestObservedAt: String? = null,
    @SerialName("recent_project_count") val recentProjectCount: Int = 0,
    @SerialName("stale_project_count") val staleProjectCount: Int = 0,
    @SerialName("last_import_outcome") val lastImportOutcome: String? = null,
)

@Serializable
data class ProjectAmenityOut(
    @SerialName("kind") val kind: String,
    @SerialName("name") val name: String,
    @SerialName("state") val state: String,
    @SerialName("distance_m") val distanceM: Int,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
    @SerialName("distance_type") val distanceType: String = "straight_line",
)

@Serializable
data class ProjectBounds(
    @SerialName("south") val south: Double,
    @SerialName("west") val west: Double,
    @SerialName("north") val north: Double,
    @SerialName("east") val east: Double,
)

@Serializable
data class ProjectBuildingInput(
    @SerialName("external_id") val externalId: String,
    @SerialName("name") val name: String,
    @SerialName("stage") val stage: String? = null,
    @SerialName("completion") val completion: String? = null,
    @SerialName("completion_date") val completionDate: String? = null,
    @SerialName("total_floors") val totalFloors: Int? = null,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
)

@Serializable
data class ProjectCitation(
    @SerialName("project_id") val projectId: String,
    @SerialName("title") val title: String,
    @SerialName("url") val url: String,
    @SerialName("observed_at") val observedAt: String,
    @SerialName("demo") val demo: Boolean,
)

@Serializable
data class ProjectCompareResponse(
    @SerialName("projects") val projects: List<ProjectOut>,
    @SerialName("rows") val rows: List<ProjectComparisonRow>,
)

@Serializable
data class ProjectComparisonRow(
    @SerialName("key") val key: String,
    @SerialName("label") val label: String,
    @SerialName("values") val values: List<ProjectComparisonValue>,
)

@Serializable
data class ProjectComparisonValue(
    @SerialName("project_id") val projectId: String,
    @SerialName("value") val value: JsonElement? = null,
    @SerialName("source_url") val sourceUrl: String? = null,
    @SerialName("observed_at") val observedAt: String? = null,
)

@Serializable
data class ProjectConversationHistory(
    @SerialName("id") val id: String,
    @SerialName("title") val title: String,
    @SerialName("criteria") val criteria: ProjectCriteria,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
    @SerialName("turns") val turns: List<ProjectHistoryTurn>,
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("next_before") val nextBefore: String? = null,
)

@Serializable
data class ProjectConversationOut(
    @SerialName("id") val id: String,
    @SerialName("title") val title: String,
    @SerialName("criteria") val criteria: ProjectCriteria,
    @SerialName("created_at") val createdAt: String,
    @SerialName("updated_at") val updatedAt: String,
)

@Serializable
data class ProjectCriteria(
    @SerialName("city") val city: String? = null,
    @SerialName("q") val q: String? = null,
    @SerialName("districts") val districts: List<String> = emptyList(),
    @SerialName("developer_names") val developerNames: List<String> = emptyList(),
    @SerialName("provider_ids") val providerIds: List<String> = emptyList(),
    @SerialName("price_mode") val priceMode: String = "published_starting_price",
    @SerialName("price_min") val priceMin: Long? = null,
    @SerialName("price_max") val priceMax: Long? = null,
    @SerialName("stages") val stages: List<String> = emptyList(),
    @SerialName("completion_before") val completionBefore: String? = null,
    @SerialName("rooms") val rooms: List<Int> = emptyList(),
    @SerialName("area_min") val areaMin: Double? = null,
    @SerialName("floor_min") val floorMin: Int? = null,
    @SerialName("floor_max") val floorMax: Int? = null,
    @SerialName("required_amenities") val requiredAmenities: List<String> = emptyList(),
    @SerialName("amenity_scope") val amenityScope: String = "nearby",
    @SerialName("amenity_radius_m") val amenityRadiusM: Int = 1000,
    @SerialName("include_stale") val includeStale: Boolean = false,
    @SerialName("bounds") val bounds: ProjectBounds? = null,
)

@Serializable
data class ProjectDocumentInput(
    @SerialName("name") val name: String,
    @SerialName("url") val url: String,
    @SerialName("kind") val kind: String = "other",
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
)

@Serializable
data class ProjectFacetValue(
    @SerialName("value") val value: String,
    @SerialName("count") val count: Int,
)

@Serializable
data class ProjectFacets(
    @SerialName("cities") val cities: List<ProjectFacetValue> = emptyList(),
    @SerialName("districts") val districts: List<ProjectFacetValue> = emptyList(),
    @SerialName("developer_names") val developerNames: List<ProjectFacetValue> = emptyList(),
    @SerialName("provider_ids") val providerIds: List<ProjectFacetValue> = emptyList(),
    @SerialName("stages") val stages: List<ProjectFacetValue> = emptyList(),
    @SerialName("price_modes") val priceModes: List<ProjectPriceFacetValue> = emptyList(),
    @SerialName("unknown_published_price_count") val unknownPublishedPriceCount: Int = 0,
    @SerialName("unknown_observed_price_count") val unknownObservedPriceCount: Int = 0,
)

@Serializable
data class ProjectFactOut(
    @SerialName("id") val id: String,
    @SerialName("kind") val kind: String,
    @SerialName("name") val name: String,
    @SerialName("state") val state: String,
    @SerialName("relation") val relation: String = "unspecified",
    @SerialName("scope_type") val scopeType: String,
    @SerialName("expected_opening") val expectedOpening: String?,
    @SerialName("evidence") val evidence: String,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
)

@Serializable
data class ProjectHistoryTurn(
    @SerialName("id") val id: String,
    @SerialName("client_turn_id") val clientTurnId: String,
    @SerialName("message") val message: String,
    @SerialName("state") val state: String,
    @SerialName("created_at") val createdAt: String,
    @SerialName("response") val response: ProjectTurnResponse? = null,
)

@Serializable
data class ProjectImageInput(
    @SerialName("url") val url: String,
    @SerialName("kind") val kind: String = "other",
    @SerialName("caption") val caption: String? = null,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
)

@Serializable
data class ProjectLayoutOut(
    @SerialName("id") val id: String,
    @SerialName("project_id") val projectId: String,
    @SerialName("external_id") val externalId: String,
    @SerialName("name") val name: String,
    @SerialName("rooms") val rooms: Int? = null,
    @SerialName("area_m2") val areaM2: Double? = null,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
    @SerialName("received_at") val receivedAt: String,
    @SerialName("provenance") val provenance: String,
    @SerialName("freshness") val freshness: String,
    @SerialName("kind") val kind: String = "layout_type",
)

@Serializable
data class ProjectOut(
    @SerialName("id") val id: String,
    @SerialName("provider_id") val providerId: String,
    @SerialName("provider_name") val providerName: String,
    @SerialName("external_id") val externalId: String,
    @SerialName("name") val name: String,
    @SerialName("city") val city: String,
    @SerialName("district") val district: String = "",
    @SerialName("address") val address: String? = null,
    @SerialName("developer_name") val developerName: String? = null,
    @SerialName("bigville_id") val bigvilleId: String? = null,
    @SerialName("bigville_name") val bigvilleName: String? = null,
    @SerialName("latitude") val latitude: Double? = null,
    @SerialName("longitude") val longitude: Double? = null,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("website_url") val websiteUrl: String? = null,
    @SerialName("website_scope") val websiteScope: String? = null,
    @SerialName("observed_at") val observedAt: String,
    @SerialName("received_at") val receivedAt: String,
    @SerialName("version") val version: Int,
    @SerialName("record_origin") val recordOrigin: String,
    @SerialName("provenance") val provenance: String,
    @SerialName("freshness") val freshness: String,
    @SerialName("stage") val stage: String? = null,
    @SerialName("completion") val completion: String? = null,
    @SerialName("completion_date") val completionDate: String? = null,
    @SerialName("finish") val finish: String? = null,
    @SerialName("published_starting_price") val publishedStartingPrice: ProjectPrice? = null,
    @SerialName("observed_listing_minimum") val observedListingMinimum: ProjectPrice? = null,
    @SerialName("display_price") val displayPrice: ProjectPrice = ProjectPrice(kind = "unknown"),
    @SerialName("buildings") val buildings: List<ProjectBuildingInput> = emptyList(),
    @SerialName("images") val images: List<ProjectImageInput> = emptyList(),
    @SerialName("documents") val documents: List<ProjectDocumentInput> = emptyList(),
    @SerialName("layouts") val layouts: List<ProjectLayoutOut> = emptyList(),
    @SerialName("published_lot_count") val publishedLotCount: Int = 0,
    @SerialName("matched_lot_count") val matchedLotCount: Int = 0,
    @SerialName("available_layout_count") val availableLayoutCount: Int = 0,
    @SerialName("amenities") val amenities: List<ProjectAmenityOut> = emptyList(),
    @SerialName("project_facts") val projectFacts: List<ProjectFactOut> = emptyList(),
)

@Serializable
data class ProjectPage(
    @SerialName("items") val items: List<ProjectOut>,
    @SerialName("total") val total: Int,
    @SerialName("next_cursor") val nextCursor: String? = null,
    @SerialName("criteria") val criteria: ProjectCriteria,
    @SerialName("sort") val sort: String,
    @SerialName("unknown_coordinates_count") val unknownCoordinatesCount: Int = 0,
)

@Serializable
data class ProjectPrice(
    @SerialName("kind") val kind: String,
    @SerialName("amount_kzt") val amountKzt: Long? = null,
    @SerialName("source_url") val sourceUrl: String? = null,
    @SerialName("observed_at") val observedAt: String? = null,
)

@Serializable
data class ProjectPriceFacetValue(
    @SerialName("value") val value: String,
    @SerialName("count") val count: Int,
)

@Serializable
data class ProjectTurnResponse(
    @SerialName("conversation_id") val conversationId: String,
    @SerialName("turn_id") val turnId: String,
    @SerialName("client_turn_id") val clientTurnId: String,
    @SerialName("mode") val mode: String = "basic",
    @SerialName("state") val state: String = "complete",
    @SerialName("action") val action: String = "search",
    @SerialName("message") val message: String,
    @SerialName("criteria") val criteria: ProjectCriteria,
    @SerialName("results") val results: ProjectPage? = null,
    @SerialName("comparison") val comparison: ProjectCompareResponse? = null,
    @SerialName("citations") val citations: List<ProjectCitation> = emptyList(),
    @SerialName("suggestions") val suggestions: List<String> = emptyList(),
    @SerialName("unsupported_conditions") val unsupportedConditions: List<String> = emptyList(),
    @SerialName("created_at") val createdAt: String,
)

@Serializable data class ProjectRequest(val criteria: ProjectCriteria, val limit: Int = 20, val cursor: String? = null, val sort: String = "price_asc")
@Serializable data class ProjectItems<T>(val items: List<T>)
typealias CatalogProject = ProjectOut
typealias ProjectLayout = ProjectLayoutOut
typealias ProjectComparison = ProjectCompareResponse
typealias ProjectConversation = ProjectConversationOut
