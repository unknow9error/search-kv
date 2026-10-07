package kz.unknown.meken.ui

import android.content.Intent
import android.net.Uri
import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.core.net.toUri
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.compose.LocalLifecycleOwner
import androidx.lifecycle.repeatOnLifecycle
import coil.compose.SubcomposeAsyncImage
import java.time.OffsetDateTime
import kz.unknown.meken.core.Apartment
import kotlinx.coroutines.delay

@Composable
internal fun ApartmentCard(apartment: Apartment, favorite: Boolean, busy: Boolean, onOpen: () -> Unit, onFavorite: () -> Unit) {
    val status = liveStatusLabel(apartment)
    Card(onClick = onOpen, border = BorderStroke(1.dp, MaterialTheme.colorScheme.outlineVariant), shape = RoundedCornerShape(12.dp), colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surface)) {
        Box {
            ApartmentArtwork(apartment, Modifier.fillMaxWidth().height(180.dp))
            FilledTonalIconButton(onClick = onFavorite, enabled = !busy, colors = IconButtonDefaults.filledTonalIconButtonColors(containerColor = MaterialTheme.colorScheme.surface, contentColor = MaterialTheme.colorScheme.primary), modifier = Modifier.align(Alignment.TopEnd).padding(10.dp)) {
                Icon(if (favorite) Icons.Outlined.Favorite else Icons.Outlined.FavoriteBorder, if (favorite) "Убрать из избранного" else "Сохранить квартиру")
            }
        }
        Column(Modifier.padding(18.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Text(apartment.priceLabel, style = MaterialTheme.typography.titleLarge)
            Text("${apartment.rooms}-комн. · ${apartment.areaLabel} · ${apartment.floor}/${apartment.totalFloors} эт.", style = MaterialTheme.typography.bodyMedium)
            Text(apartment.complexName, style = MaterialTheme.typography.titleMedium)
            Text("${apartment.city} · ${apartment.district.ifEmpty { apartment.address }}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            apartment.reasons.firstOrNull()?.let { Text(it, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.primary) }
            Text(status, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            if (!apartment.isDemo) Text("${if (apartment.provenance == "provider") apartment.providerName else "Источник не подтверждён"} · ${apartment.observedAtLabel}", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}

@Composable
internal fun ApartmentArtwork(apartment: Apartment, modifier: Modifier = Modifier) {
    Surface(modifier, color = MaterialTheme.colorScheme.surfaceVariant) {
        if (apartment.safeImageUrl != null) SubcomposeAsyncImage(
            model = apartment.safeImageUrl, contentDescription = "Изображение от застройщика", contentScale = ContentScale.Fit,
            loading = { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { CircularProgressIndicator(Modifier.size(24.dp)) } },
            error = { ImageUnavailable(apartment.isDemo) }, modifier = Modifier.fillMaxSize().padding(10.dp),
        ) else ImageUnavailable(apartment.isDemo)
    }
}

@Composable
private fun ImageUnavailable(demo: Boolean) {
    Column(Modifier.fillMaxSize(), horizontalAlignment = Alignment.CenterHorizontally, verticalArrangement = Arrangement.Center) {
        Icon(Icons.Outlined.Apartment, contentDescription = null, modifier = Modifier.size(46.dp), tint = MaterialTheme.colorScheme.primary)
        Spacer(Modifier.height(10.dp))
        Text(if (demo) "Демонстрационный объект" else "Изображение не предоставлено", style = MaterialTheme.typography.bodySmall)
    }
}

@Composable
internal fun ApartmentDetail(initial: Apartment, searchBusy: Boolean, model: MekenViewModel, onDiscuss: (Apartment) -> Unit, onRefresh: (Apartment) -> Unit, canVerify: Boolean = true) {
    var refreshed by remember(initial.id) { mutableStateOf<Apartment?>(null) }
    var checking by remember(initial.id) { mutableStateOf(false) }
    var verificationMessage by remember(initial.id) { mutableStateOf<String?>(null) }
    val apartment = refreshed ?: initial
    val status = liveStatusLabel(apartment)
    val context = LocalContext.current
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(22.dp), verticalArrangement = Arrangement.spacedBy(22.dp)) {
        item { Surface(shape = RoundedCornerShape(12.dp)) { ApartmentArtwork(apartment, Modifier.fillMaxWidth().height(260.dp)) } }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(10.dp)) {
                Text(apartment.complexName, style = MaterialTheme.typography.headlineMedium)
                Text(apartment.priceLabel, style = MaterialTheme.typography.titleLarge)
                Text("${apartment.rooms}-комнатная · ${apartment.areaLabel} · ${apartment.floor} из ${apartment.totalFloors} этажей", style = MaterialTheme.typography.bodyMedium)
                Text(apartment.address, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        item { NoticeCard(if (apartment.isDemo) "Пример квартиры для знакомства с приложением. Адрес, цена и объекты рядом вымышлены; покупка недоступна." else "$status. Сведения получены ${apartment.observedAtLabel}. Цена и наличие могут измениться.") }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Text("Почему стоит посмотреть", style = MaterialTheme.typography.titleMedium)
                if (apartment.reasons.isEmpty()) Text("Укажите бюджет, комнаты и пожелания в подборе — я смогу сопоставить их с этой квартирой.", style = MaterialTheme.typography.bodyMedium)
                apartment.reasons.forEach { ReasonRow(it, positive = true) }
                apartment.tradeoffs.forEach { ReasonRow(it, positive = false) }
                TextButton(onClick = { onDiscuss(apartment) }, enabled = !searchBusy) {
                    Icon(Icons.Outlined.ChatBubbleOutline, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text("Обсудить эту квартиру")
                }
            }
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                HorizontalDivider()
                DetailRow("Отделка", apartment.finish)
                DetailRow("Срок", apartment.completion)
                DetailRow("Застройщик", apartment.providerName)
                Text("Инфраструктура проекта", style = MaterialTheme.typography.titleMedium)
                apartment.bigvilleName?.let { Text("Бигвилль «$it»", style = MaterialTheme.typography.bodyMedium) }
                if (apartment.projectFacts.isEmpty()) Text("В подключённых данных застройщика пока нет подробного описания инфраструктуры. Это не означает, что школы или садика нет.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                apartment.projectFacts.forEach { fact ->
                    Surface(color = MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(10.dp)) {
                        Column(Modifier.fillMaxWidth().padding(14.dp), verticalArrangement = Arrangement.spacedBy(7.dp)) {
                            Text(fact.name, style = MaterialTheme.typography.titleSmall)
                            Text("${fact.statusLabel} · ${fact.scopeLabel}", style = MaterialTheme.typography.bodySmall, color = if (fact.state == "operating") MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.secondary)
                            fact.expectedOpening?.let { Text("Заявленный срок: $it", style = MaterialTheme.typography.bodySmall) }
                            Text(fact.evidence, style = MaterialTheme.typography.bodySmall)
                            if (!apartment.isDemo && fact.safeSourceUrl != null) TextButton(onClick = { openHttps(context, fact.safeSourceUrl) }) { Text("Источник застройщика") }
                        }
                    }
                }
            }
        }
        val latitude = apartment.latitude
        val longitude = apartment.longitude
        if (!apartment.isDemo && latitude != null && longitude != null && latitude.isFinite() && longitude.isFinite() && latitude in -90.0..90.0 && longitude in -180.0..180.0) item {
            OutlinedButton(onClick = {
                val query = Uri.encode("$latitude,$longitude(${apartment.complexName})")
                openIntent(context, Intent(Intent.ACTION_VIEW, "geo:$latitude,$longitude?q=$query".toUri()))
            }, modifier = Modifier.fillMaxWidth()) { Icon(Icons.Outlined.Map, null); Spacer(Modifier.width(8.dp)); Text("Показать расположение на карте") }
        }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text("Дополнительно: объекты рядом", style = MaterialTheme.typography.titleMedium)
                if (apartment.amenities.isEmpty()) Text("Пока нет подтверждённых данных о школах, садиках и парках рядом. Отсутствие данных не означает, что их нет.", style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
                apartment.amenities.forEach { amenity ->
                    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                        Icon(when (amenity.kind) { "school" -> Icons.Outlined.School; "kindergarten" -> Icons.Outlined.ChildCare; "park" -> Icons.Outlined.Park; else -> Icons.Outlined.Tram }, null, tint = MaterialTheme.colorScheme.primary)
                        Column(Modifier.weight(1f)) {
                            Text(amenity.name, style = MaterialTheme.typography.bodyMedium)
                            if (!apartment.isDemo && amenity.safeSourceUrl != null) TextButton(onClick = { openHttps(context, amenity.safeSourceUrl) }, contentPadding = PaddingValues(0.dp)) { Text("Источник", style = MaterialTheme.typography.bodySmall) }
                        }
                        Text("${amenity.distanceM} м", style = MaterialTheme.typography.bodyMedium)
                    }
                }
                if (!apartment.isDemo && apartment.amenities.any { it.safeSourceUrl?.let { source -> source.toUri().host?.let { host -> host == "openstreetmap.org" || host.endsWith(".openstreetmap.org") } } == true }) {
                    TextButton(onClick = { openHttps(context, "https://www.openstreetmap.org/copyright") }) { Text("© OpenStreetMap contributors · ODbL", style = MaterialTheme.typography.labelSmall) }
                }
                Text("Расстояния по прямой. Пеший маршрут и возможность зачисления в школу проверяются отдельно.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
        verificationMessage?.let { text -> item { NoticeCard(text) } }
        item {
            Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
                Button(onClick = {
                    checking = true
                    model.verify(apartment) { result, error ->
                        checking = false
                        result?.listing?.let { refreshed = it; onRefresh(it) }
                        verificationMessage = error ?: if (result?.verification == "confirmed") "Получен ответ застройщика. Текущий статус: ${(result.listing ?: apartment).statusLabel.lowercase()}. Это не бронирование." else "Не удалось получить новое подтверждение наличия. Можно повторить проверку позже или обратиться к застройщику."
                    }
                }, enabled = canVerify && !checking && !apartment.isDemo, modifier = Modifier.fillMaxWidth(), contentPadding = PaddingValues(17.dp)) {
                    if (checking) { CircularProgressIndicator(Modifier.size(20.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary); Spacer(Modifier.width(8.dp)) }
                    Text(if (checking) "Проверяю наличие" else "Проверить наличие")
                }
                if (!canVerify) Text("Подключитесь к сервису, чтобы проверить цену и наличие.", style = MaterialTheme.typography.bodySmall)
                if (apartment.canOpenSource) TextButton(onClick = { openHttps(context, apartment.safeSourceUrl) }, modifier = Modifier.fillMaxWidth()) {
                    Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text("Открыть у застройщика")
                }
            }
        }
    }
}

@Composable
private fun ReasonRow(text: String, positive: Boolean) {
    Row(horizontalArrangement = Arrangement.spacedBy(9.dp)) {
        Icon(if (positive) Icons.Outlined.CheckCircle else Icons.Outlined.Info, null, Modifier.size(20.dp), tint = if (positive) MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant)
        Text(text, style = MaterialTheme.typography.bodyMedium)
    }
}

@Composable
private fun DetailRow(title: String, value: String) {
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(20.dp)) {
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text(value, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, textAlign = androidx.compose.ui.text.style.TextAlign.End)
    }
}

/** Expire a recent status while the screen stays open, including after a background return. */
@Composable
private fun liveStatusLabel(apartment: Apartment): String {
    val owner = LocalLifecycleOwner.current
    val label by produceState(apartment.statusLabel, apartment, owner) {
        owner.lifecycle.repeatOnLifecycle(Lifecycle.State.STARTED) {
            while (true) {
                value = apartment.statusLabel
                val expiry = runCatching { OffsetDateTime.parse(apartment.observedAt).toInstant().plusSeconds(300).toEpochMilli() }.getOrNull()
                val remaining = expiry?.minus(System.currentTimeMillis())
                delay(if (remaining != null && remaining >= 0) (remaining + 10).coerceIn(50, 60_000) else 60_000)
            }
        }
    }
    return label
}
