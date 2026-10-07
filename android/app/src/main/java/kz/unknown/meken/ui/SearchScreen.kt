package kz.unknown.meken.ui

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.lazy.rememberLazyListState
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.OpenInNew
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.CornerRadius
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.geometry.Size
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.ChatMessage
import kz.unknown.meken.core.Preferences

@Composable
internal fun SearchScreen(state: MekenUiState, model: MekenViewModel, onFilters: (String?) -> Unit, onOpen: (Apartment) -> Unit, onAbout: () -> Unit) {
    val search = state.search
    val listState = rememberLazyListState()
    var draft by rememberSaveable { mutableStateOf("") }
    var showingConversation by rememberSaveable { mutableStateOf(false) }
    LaunchedEffect(search.messages.isEmpty()) { if (search.messages.isEmpty()) showingConversation = false }
    val focus = LocalFocusManager.current
    val latestUserId = search.messages.lastOrNull { it.isUser }?.id
    LaunchedEffect(latestUserId) {
        val messageIndex = search.messages.indexOfLast { it.id == latestUserId }
        if (messageIndex >= 0) {
            val offset = (if (state.config?.isDemo == true) 1 else 0) + (if (search.notice != null) 1 else 0) + (if (state.favoriteError != null) 1 else 0) + (if (state.hasMoreHistory || state.isLoadingOlder) 1 else 0)
            listState.animateScrollToItem(offset + messageIndex)
        }
    }
    Column(Modifier.fillMaxSize()) {
        LazyColumn(Modifier.weight(1f).fillMaxWidth(), state = listState, contentPadding = PaddingValues(horizontal = 22.dp, vertical = 12.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
            if (state.config?.isDemo == true) item { NoticeCard("Демо-каталог. Квартиры не продаются.") }
            if (search.messages.isEmpty()) item { SearchForm(state, onFilters, onApply = model::applyFilters, onSend = { model.send(it) }, onTalk = { showingConversation = true; draft = it }, onAbout = onAbout, onResume = model::restoreConversation) }
            search.notice?.let { notice -> item { NoticeCard(notice) } }
            state.favoriteError?.let { error -> item { NoticeCard(error) } }
            if (state.hasMoreHistory || state.isLoadingOlder) item(key = "load-earlier") {
                OutlinedButton(onClick = model::loadOlderHistory, enabled = !state.isLoadingOlder && !search.isStreaming, modifier = Modifier.fillMaxWidth()) {
                    if (state.isLoadingOlder) {
                        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                        Spacer(Modifier.width(8.dp))
                    }
                    Text(if (state.isLoadingOlder) "Загружаю сообщения" else "Показать более ранние сообщения")
                }
            }
            items(search.messages, key = { it.id }) { MessageBubble(it) }
            if (search.apartments.isNotEmpty()) item {
                Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                    Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(4.dp)) {
                        Text("Ваша подборка", style = MaterialTheme.typography.titleLarge)
                        Text("${search.apartments.size} вариантов · ${search.preferences.city ?: "Все города"}", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                    }
                    IconButton(onClick = { onFilters(search.preferences.city) }) { Icon(Icons.Outlined.Tune, "Открыть фильтры") }
                }
            }
            items(search.apartments, key = { "listing:${it.id}" }) { apartment ->
                Column(verticalArrangement = Arrangement.spacedBy(4.dp)) {
                    ApartmentCard(apartment, state.favorites.any { it.id == apartment.id }, apartment.id in state.busyFavorites,
                        onOpen = { onOpen(apartment) }, onFavorite = { model.toggleFavorite(apartment) })
                    val selected = apartment.id in state.selectedForComparison
                    TextButton(onClick = { model.toggleComparison(apartment.id) }, enabled = selected || state.selectedForComparison.size < 3) {
                        Icon(if (selected) Icons.Outlined.CheckCircle else Icons.Outlined.AddCircleOutline, null, Modifier.size(20.dp))
                        Spacer(Modifier.width(7.dp)); Text(if (selected) "Добавлена к сравнению" else "Сравнить")
                    }
                }
            }
            if (state.selectedForComparison.size >= 2) item {
                Button(onClick = model::compare, enabled = !search.isStreaming && !search.isPending, modifier = Modifier.fillMaxWidth()) { Text("Сравнить выбранные (${state.selectedForComparison.size})") }
            }
            if (search.isStreaming) item {
                Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                    CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp)
                    Text(search.status.ifEmpty { "Ищу подходящие варианты" }, style = MaterialTheme.typography.bodySmall)
                }
            }
            search.error?.let { error -> item { NoticeCard(error) } }
            if (search.isPending && !search.isStreaming) item {
                TextButton(onClick = model::resume) { Text("Проверить сохранённый ответ") }
            }
            if (search.suggestions.isNotEmpty() && !search.isStreaming) item {
                Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                    search.suggestions.forEach { text -> SuggestionChip(onClick = { model.send(text) }, enabled = !search.isPending, label = { Text(text) }) }
                }
            }
        }
        if (showingConversation || search.messages.isNotEmpty()) Surface(color = MaterialTheme.colorScheme.background) {
            Row(Modifier.fillMaxWidth().padding(horizontal = 12.dp, vertical = 8.dp), verticalAlignment = Alignment.Bottom, horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                IconButton(onClick = { onFilters(search.preferences.city) }) { Icon(Icons.Outlined.Tune, "Фильтры поиска") }
                OutlinedTextField(
                    value = draft,
                    onValueChange = { value -> draft = if (value.codePointCount(0, value.length) <= 2000) value else value.substring(0, value.offsetByCodePoints(0, 2000)) },
                    modifier = Modifier.weight(1f), maxLines = 5, shape = RoundedCornerShape(22.dp),
                    placeholder = { Text("Уточнить пожелания") },
                    supportingText = if (draft.codePointCount(0, draft.length) >= 1900) ({ Text("${draft.codePointCount(0, draft.length)}/2000") }) else null,
                )
                if (search.isStreaming) FilledIconButton(onClick = model::stop) { Icon(Icons.Outlined.Stop, "Остановить подбор") }
                else FilledIconButton(onClick = { val text = draft.trim(); draft = ""; focus.clearFocus(); model.send(text) }, enabled = draft.isNotBlank() && !search.isPending) {
                    Icon(Icons.Outlined.ArrowUpward, "Отправить сообщение")
                }
            }
        }
    }
}

@Composable
private fun MessageBubble(message: ChatMessage) {
    val context = LocalContext.current
    Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
        if (message.isUser) Spacer(Modifier.width(26.dp)) else BrandMark()
        Surface(Modifier.weight(1f), color = if (message.isUser) MaterialTheme.colorScheme.primaryContainer else MaterialTheme.colorScheme.surfaceVariant, shape = RoundedCornerShape(12.dp)) {
            Column(Modifier.padding(if (message.isUser) 15.dp else 12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                SelectionContainer { Text(message.text, style = MaterialTheme.typography.bodyMedium) }
                message.citations.forEach { citation ->
                    if (citation.safeUrl != null) TextButton(onClick = { openHttps(context, citation.safeUrl) }, contentPadding = PaddingValues(0.dp)) {
                        Icon(Icons.AutoMirrored.Outlined.OpenInNew, null, Modifier.size(16.dp)); Spacer(Modifier.width(7.dp)); Text(citation.title, style = MaterialTheme.typography.bodySmall)
                    } else if (!citation.demo) Text(citation.title, style = MaterialTheme.typography.bodySmall)
                }
            }
        }
        if (!message.isUser) Spacer(Modifier.width(4.dp))
    }
}

@Composable
private fun SearchForm(
    state: MekenUiState, onFilters: (String?) -> Unit, onApply: (Preferences) -> Unit,
    onSend: (String) -> Unit, onTalk: (String) -> Unit, onAbout: () -> Unit, onResume: (String) -> Unit,
) {
    val preferences = state.search.preferences
    val cities = state.config?.cities.orEmpty()
    var city by rememberSaveable(preferences.city, cities) {
        mutableStateOf(preferences.city ?: cities.firstOrNull { it == "Астана" } ?: cities.firstOrNull().orEmpty())
    }
    var cityMenu by remember { mutableStateOf(false) }
    val busy = state.search.isStreaming || state.search.isPending || state.isDeleting || state.isRecovering
    val budget = preferences.budgetMax?.let {
        val number = java.text.NumberFormat.getNumberInstance(java.util.Locale.forLanguageTag("ru-KZ")).apply { maximumFractionDigits = 2 }
        "До ${number.format(it / 1_000_000.0)} млн ₸"
    } ?: "Без ограничений"
    Column(verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text("Поиск квартир", style = MaterialTheme.typography.headlineMedium, modifier = Modifier.padding(bottom = 6.dp))
        Box {
            SearchFormRow("", city.ifEmpty { "Выберите город" }, Icons.Outlined.LocationOn, enabled = !busy, onClick = { cityMenu = true })
            DropdownMenu(expanded = cityMenu, onDismissRequest = { cityMenu = false }) {
                cities.forEach { value -> DropdownMenuItem(text = { Text(value) }, onClick = { city = value; cityMenu = false }) }
            }
        }
        SearchFormRow("Бюджет квартиры", budget, Icons.Outlined.Payments, !busy) { onFilters(city.ifEmpty { null }) }
        SearchFormRow("Комнаты", preferences.rooms.sorted().joinToString(", ").ifEmpty { "Любое количество" }, Icons.Outlined.GridView, !busy) { onFilters(city.ifEmpty { null }) }
        Row(Modifier.horizontalScroll(rememberScrollState()), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            SuggestionChip(onClick = { onSend("Астана до 35 млн ₸") }, enabled = !busy, label = { Text("Астана до 35 млн ₸") }, shape = RoundedCornerShape(8.dp))
            SuggestionChip(onClick = { onTalk(if (city.isEmpty()) "Нужна школа рядом" else "Ищу квартиру. Город: $city. Нужна школа рядом") }, enabled = !busy, label = { Text("Школа рядом") }, icon = { Icon(Icons.Outlined.School, null, Modifier.size(16.dp)) }, shape = RoundedCornerShape(8.dp))
        }
        SearchFormRow("", "Все фильтры", Icons.Outlined.Tune, !busy) { onFilters(city.ifEmpty { null }) }
        Button(onClick = { onApply(preferences.copy(city = city.ifEmpty { null })) }, enabled = !busy && city.isNotEmpty(),
            modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(8.dp), contentPadding = PaddingValues(15.dp)) {
            Icon(Icons.Outlined.Search, null, Modifier.size(20.dp)); Spacer(Modifier.width(8.dp)); Text("Найти квартиры")
        }
        Surface(onClick = { onTalk(if (city.isEmpty()) "" else "Ищу квартиру. Город: $city. ") }, enabled = !busy, modifier = Modifier.fillMaxWidth().padding(top = 6.dp),
            color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(10.dp)) {
            Row(Modifier.padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
                Icon(Icons.Outlined.ChatBubbleOutline, null, Modifier.size(22.dp))
                Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                    Text("Уточнить в разговоре", style = MaterialTheme.typography.bodyMedium, fontWeight = FontWeight.Medium)
                    Text("Опишите, что важно для вас", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                }
                Icon(Icons.Outlined.ChevronRight, null, Modifier.size(18.dp))
            }
        }
        state.conversations.firstOrNull()?.let { recent ->
            Text("Последний поиск", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant, modifier = Modifier.padding(top = 12.dp))
            SearchFormRow("", recent.title, Icons.Outlined.History, !busy) { onResume(recent.id) }
        }
        TextButton(onClick = onAbout, modifier = Modifier.fillMaxWidth()) {
            Icon(Icons.Outlined.Info, null, Modifier.size(16.dp)); Spacer(Modifier.width(8.dp))
            Text("О каталоге и источниках", Modifier.weight(1f), style = MaterialTheme.typography.bodySmall)
            Icon(Icons.Outlined.ChevronRight, null, Modifier.size(16.dp))
        }
    }
}

@Composable
private fun SearchFormRow(title: String, value: String, icon: androidx.compose.ui.graphics.vector.ImageVector, enabled: Boolean, onClick: () -> Unit) {
    Surface(onClick = onClick, enabled = enabled, modifier = Modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.surfaceVariant, contentColor = MaterialTheme.colorScheme.onSurface, shape = RoundedCornerShape(10.dp)) {
        Row(Modifier.heightIn(min = 52.dp).padding(14.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(12.dp)) {
            Icon(icon, null, Modifier.size(22.dp), tint = MaterialTheme.colorScheme.primary)
            Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(3.dp)) {
                if (title.isNotEmpty()) Text(title, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value, style = MaterialTheme.typography.bodyMedium)
            }
            Icon(Icons.Outlined.ChevronRight, null, Modifier.size(16.dp), tint = MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
