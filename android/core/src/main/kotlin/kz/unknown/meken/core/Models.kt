@file:OptIn(kotlinx.serialization.ExperimentalSerializationApi::class)

package kz.unknown.meken.core

import java.net.URI
import java.text.NumberFormat
import java.time.Clock
import java.time.Duration
import java.time.Instant
import java.time.OffsetDateTime
import java.time.ZoneId
import java.time.format.DateTimeFormatter
import java.util.Locale
import kotlinx.serialization.SerialName
import kotlinx.serialization.Serializable
import kotlinx.serialization.json.Json
import kotlinx.serialization.json.JsonObject

/** One wire format for HTTP responses, requests, persisted event payloads and SSE. */
val API_JSON = Json {
    ignoreUnknownKeys = true
    encodeDefaults = true
    explicitNulls = false
}

@Serializable
data class Preferences(
    val city: String? = null,
    @SerialName("budget_max") val budgetMax: Long? = null,
    val rooms: List<Int> = emptyList(),
    @SerialName("area_min") val areaMin: Double? = null,
    @SerialName("floor_min") val floorMin: Int? = null,
    @SerialName("floor_max") val floorMax: Int? = null,
    @SerialName("preferred_amenities") val preferredAmenities: List<String> = emptyList(),
    @SerialName("required_amenities") val requiredAmenities: List<String> = emptyList(),
    @SerialName("amenity_radius_m") val amenityRadiusM: Int = 1000,
    @SerialName("amenity_scope") val amenityScope: String = "nearby",
)

@Serializable
data class Apartment(
    val id: String,
    @SerialName("provider_id") val providerId: String,
    @SerialName("provider_name") val providerName: String,
    @SerialName("complex_name") val complexName: String,
    val city: String,
    val district: String,
    val address: String,
    val rooms: Int,
    @SerialName("area_m2") val areaM2: Double,
    val floor: Int,
    @SerialName("total_floors") val totalFloors: Int,
    @SerialName("price_kzt") val priceKzt: Long,
    val status: String,
    val latitude: Double? = null,
    val longitude: Double? = null,
    val finish: String,
    val completion: String,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("image_url") val imageUrl: String? = null,
    @SerialName("observed_at") val observedAt: String,
    val version: Int,
    val provenance: String,
    val freshness: String,
    val reasons: List<String>,
    val tradeoffs: List<String>,
    val amenities: List<Amenity>,
    @SerialName("bigville_name") val bigvilleName: String? = null,
    @SerialName("project_facts") val projectFacts: List<ProjectFact> = emptyList(),
) {
    val isDemo: Boolean get() = provenance == "demo"
    val safeSourceUrl: String? get() = safeHttpsUrl(sourceUrl)
    val safeImageUrl: String? get() = safeHttpsUrl(imageUrl)
    val canOpenSource: Boolean get() = provenance == "provider" && safeSourceUrl != null
    val isRecent: Boolean get() = isRecentAt(Clock.systemUTC())

    fun isRecentAt(clock: Clock): Boolean {
        val observed = parseInstant(observedAt) ?: return false
        val age = Duration.between(observed, clock.instant())
        return freshness == "recent" && !age.isNegative && age <= Duration.ofMinutes(5)
    }

    val statusLabel: String get() = when {
        isDemo -> "Демонстрационный вариант"
        status == "sold" -> "Продана по данным застройщика"
        status == "reserved" -> "Забронирована"
        status == "available" -> if (isRecent) "В предложениях застройщика" else "Наличие нужно уточнить"
        else -> "Наличие не подтверждено"
    }
    val priceLabel: String get() = NumberFormat.getIntegerInstance(RU_KZ).format(priceKzt) + " ₸"
    val areaLabel: String get() = NumberFormat.getNumberInstance(RU_KZ).apply {
        minimumFractionDigits = 0
        maximumFractionDigits = 1
    }.format(areaM2) + " м²"
    val observedAtLabel: String get() = parseInstant(observedAt)?.let {
        OBSERVED_AT_FORMAT.format(it)
    } ?: "Время проверки не указано"
}

@Serializable
data class Amenity(
    val kind: String,
    val name: String,
    @SerialName("distance_m") val distanceM: Int,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
    @SerialName("distance_type") val distanceType: String = "straight_line",
) {
    val id: String get() = "$kind:$name:$sourceUrl"
    val safeSourceUrl: String? get() = safeHttpsUrl(sourceUrl)
}

@Serializable
data class Citation(val id: String, val title: String, val url: String, val demo: Boolean) {
    val safeUrl: String? get() = if (demo) null else safeHttpsUrl(url)
}

@Serializable
data class ProjectFact(
    val id: String,
    val kind: String,
    val name: String,
    val state: String,
    @SerialName("scope_type") val scopeType: String,
    val relation: String = "unspecified",
    @SerialName("expected_opening") val expectedOpening: String? = null,
    val evidence: String,
    @SerialName("source_url") val sourceUrl: String,
    @SerialName("observed_at") val observedAt: String,
) {
    val safeSourceUrl: String? get() = safeHttpsUrl(sourceUrl)
    val scopeLabel: String get() = when (relation) {
        "within" -> if (scopeType == "bigville") "В бигвилле" else "В ЖК"
        "nearby" -> if (scopeType == "bigville") "Рядом с бигвиллем" else "Рядом с ЖК"
        else -> if (scopeType == "bigville") "В описании бигвилля" else "В описании ЖК"
    }
    val statusLabel: String get() = when (state) {
        "operating" -> "Действует по данным застройщика"
        "planned" -> "Запланировано"
        "under_construction" -> "Строится"
        else -> "Статус уточняется"
    }
}

@Serializable
data class AppConfig(
    val mode: String,
    @SerialName("ai_enabled") val aiEnabled: Boolean,
    val cities: List<String>,
    @SerialName("privacy_url") val privacyUrl: String? = null,
    @SerialName("terms_url") val termsUrl: String? = null,
    @SerialName("retention_days") val retentionDays: Int,
    val capabilities: List<String> = emptyList(),
) {
    val isDemo: Boolean get() = mode == "demo"
}

@Serializable
data class TokenPair(
    @SerialName("access_token") val accessToken: String,
    @SerialName("refresh_token") val refreshToken: String,
    @SerialName("expires_in") val expiresIn: Int,
    @SerialName("user_id") val userId: String,
)

@Serializable
data class ConversationSummary(val id: String, val title: String, val preferences: Preferences)

@Serializable
data class ConversationHistory(
    val id: String,
    val title: String,
    val preferences: Preferences,
    val turns: List<HistoryTurn>,
    @SerialName("has_more") val hasMore: Boolean = false,
    @SerialName("next_before") val nextBefore: String? = null,
)

@Serializable
data class HistoryTurn(val id: String, val message: String, val status: String, val events: List<HistoryEvent>)

@Serializable
data class HistoryEvent(val sequence: Int, val kind: String, val payload: JsonObject) {
    fun streamEvent(): ServerEvent = decodeEvent(kind, payload.toString())
}

@Serializable
data class Items<T>(val items: List<T>)

@Serializable
data class Verification(
    val listing: Apartment? = null,
    val verification: String,
    @SerialName("checked_at") val checkedAt: String,
)

/** Validate all externally opened URLs, including sources, legal links and images. */
fun safeHttpsUrl(raw: String?): String? {
    if (raw.isNullOrEmpty()) return null
    val uri = runCatching { URI(raw) }.getOrNull() ?: return null
    if (!uri.scheme.equals("https", ignoreCase = true) || uri.host.isNullOrEmpty() ||
        uri.rawUserInfo != null || uri.isOpaque || (uri.port != -1 && uri.port !in 1..65535)
    ) return null
    return uri.toASCIIString()
}

private val RU_KZ = Locale.forLanguageTag("ru-KZ")
private val OBSERVED_AT_FORMAT = DateTimeFormatter.ofPattern("d MMMM yyyy, HH:mm", RU_KZ)
    .withZone(ZoneId.of("Asia/Almaty"))
private fun parseInstant(raw: String): Instant? = runCatching { OffsetDateTime.parse(raw).toInstant() }.getOrNull()
