package kz.unknown.meken.ui

import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.ExpandMore
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kz.unknown.meken.core.Preferences
import kotlin.math.roundToLong

@Composable
internal fun FiltersScreen(initial: Preferences, cities: List<String>, busy: Boolean, onApply: (Preferences) -> Unit, onClose: () -> Unit) {
    var city by rememberSaveable { mutableStateOf(initial.city ?: "") }
    var budget by rememberSaveable { mutableStateOf(initial.budgetMax?.let { (it / 1_000_000.0).toString() } ?: "") }
    var area by rememberSaveable { mutableStateOf(initial.areaMin?.toString() ?: "") }
    var floorMin by rememberSaveable { mutableStateOf(initial.floorMin?.toString() ?: "") }
    var floorMax by rememberSaveable { mutableStateOf(initial.floorMax?.toString() ?: "") }
    var rooms by rememberSaveable { mutableStateOf(initial.rooms.joinToString(",")) }
    var preferred by rememberSaveable { mutableStateOf(initial.preferredAmenities.joinToString(",")) }
    var required by rememberSaveable { mutableStateOf(initial.requiredAmenities.joinToString(",")) }
    var scope by rememberSaveable { mutableStateOf(initial.amenityScope) }
    var radius by rememberSaveable { mutableIntStateOf(initial.amenityRadiusM) }
    var error by rememberSaveable { mutableStateOf<String?>(null) }
    Column(Modifier.fillMaxSize()) {
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(22.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            TextButton(onClick = { city = ""; budget = ""; area = ""; floorMin = ""; floorMax = ""; rooms = ""; preferred = ""; required = ""; scope = "nearby"; radius = 1000; error = null }, modifier = Modifier.fillMaxWidth()) { Text("Сбросить фильтры") }
            Text("Где ищем", style = MaterialTheme.typography.titleMedium)
            ChoiceMenu("Город", city.ifBlank { "Все города" }, listOf("" to "Все города") + cities.map { it to it }) { city = it }
            NumericField("Общий бюджет, млн ₸", budget, { budget = it }, "Например, 35")
            Text("Полная стоимость квартиры. Оставьте пустым, если пока не определились.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Text("Количество комнат", style = MaterialTheme.typography.titleMedium)
            Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                (1..8).forEach { room ->
                    val selected = room.toString() in csvSet(rooms)
                    FilterChip(selected = selected, onClick = { rooms = (csvSet(rooms).toMutableSet().apply { if (selected) remove(room.toString()) else add(room.toString()) }).joinToString(",") }, label = { Text("$room комн.") })
                }
            }
            NumericField("Минимальная площадь, м²", area, { area = it }, "От 10 до 1 000")
            Row(horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                NumericField("Этаж от", floorMin, { floorMin = it }, "1", Modifier.weight(1f), integer = true)
                NumericField("Этаж до", floorMax, { floorMax = it }, "150", Modifier.weight(1f), integer = true)
            }
            Text("Что должно быть рядом", style = MaterialTheme.typography.titleMedium)
            listOf("school" to "Школа", "kindergarten" to "Детский сад", "park" to "Парк", "transit" to "Остановка").forEach { (kind, title) ->
                val priority = when (kind) { in csvSet(required) -> "required"; in csvSet(preferred) -> "preferred"; else -> "none" }
                ChoiceMenu(title, when (priority) { "required" -> "Обязательно"; "preferred" -> "Желательно"; else -> "Не важно" }, listOf("none" to "Не важно", "preferred" to "Желательно", "required" to "Обязательно")) { value ->
                    preferred = csvSet(preferred).toMutableSet().apply { remove(kind); if (value == "preferred") add(kind) }.joinToString(",")
                    required = csvSet(required).toMutableSet().apply { remove(kind); if (value == "required") add(kind) }.joinToString(",")
                }
            }
            val scopes = listOf("nearby" to "Рядом по расстоянию", "complex" to "В этом ЖК", "bigville" to "В бигвилле")
            ChoiceMenu("Область поиска", scopes.firstOrNull { it.first == scope }?.second ?: "Рядом по расстоянию", scopes) { scope = it }
            if (scope == "nearby") ChoiceMenu("Радиус", "$radius м", (listOf(100, 500, 1000, 2000, 5000) + radius).distinct().sorted().map { it.toString() to "$it м" }) { radius = it.toInt() }
            NoticeCard("Для поиска рядом используются расстояния по прямой; для ЖК и бигвилля — сведения застройщика. «Обязательно» требует подтверждения действующего объекта. Запланированная школа не считается работающей.")
            error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }
        }
        Button(onClick = {
            val millions = budget.normalizedDouble()
            val minArea = area.normalizedDouble()
            val minFloor = floorMin.trim().toIntOrNull()
            val maxFloor = floorMax.trim().toIntOrNull()
            error = when {
                budget.isNotBlank() && (millions == null || !millions.isFinite() || millions !in 1.0..10_000.0) -> "Укажите бюджет от 1 до 10 000 млн ₸."
                area.isNotBlank() && (minArea == null || !minArea.isFinite() || minArea !in 10.0..1000.0) -> "Укажите площадь от 10 до 1 000 м²."
                floorMin.isNotBlank() && (minFloor == null || minFloor !in 1..150) -> "Минимальный этаж должен быть от 1 до 150."
                floorMax.isNotBlank() && (maxFloor == null || maxFloor !in 1..150) -> "Максимальный этаж должен быть от 1 до 150."
                minFloor != null && maxFloor != null && minFloor > maxFloor -> "Минимальный этаж не может быть выше максимального."
                else -> null
            }
            if (error == null) onApply(Preferences(city = city.ifBlank { null }, budgetMax = millions?.times(1_000_000)?.roundToLong(), rooms = csvSet(rooms).mapNotNull { it.toIntOrNull() }.sorted(), areaMin = minArea, floorMin = minFloor, floorMax = maxFloor,
                preferredAmenities = csvSet(preferred).toList(), requiredAmenities = csvSet(required).toList(), amenityScope = scope, amenityRadiusM = radius))
        }, enabled = !busy, modifier = Modifier.fillMaxWidth().padding(horizontal = 22.dp, vertical = 12.dp), shape = RoundedCornerShape(8.dp), contentPadding = PaddingValues(17.dp)) { Text("Показать квартиры") }

    }
}

@Composable
private fun NumericField(label: String, value: String, onChange: (String) -> Unit, placeholder: String, modifier: Modifier = Modifier, integer: Boolean = false) {
    OutlinedTextField(value = value, onValueChange = { if (it.length <= 20) onChange(it) }, modifier = modifier.fillMaxWidth(), singleLine = true, shape = RoundedCornerShape(10.dp),
        label = { Text(label) }, placeholder = { Text(placeholder) }, keyboardOptions = KeyboardOptions(keyboardType = if (integer) KeyboardType.Number else KeyboardType.Decimal))
}

@Composable
private fun ChoiceMenu(title: String, selected: String, options: List<Pair<String, String>>, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        Surface(onClick = { expanded = true }, modifier = Modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surfaceVariant, contentColor = MaterialTheme.colorScheme.onSurface, shape = RoundedCornerShape(10.dp)) {
            Row(Modifier.heightIn(min = 56.dp).padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text(title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    Text(selected, style = MaterialTheme.typography.bodyMedium)
                }
                Icon(Icons.Outlined.ExpandMore, contentDescription = null, Modifier.size(20.dp))
            }
        }
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (value, label) -> DropdownMenuItem(text = { Text(label) }, onClick = { expanded = false; onSelect(value) }) }
        }
    }
}

private fun csvSet(value: String): Set<String> = value.split(',').filter { it.isNotBlank() }.toSet()
private fun String.normalizedDouble(): Double? = trim().replace(',', '.').toDoubleOrNull()
