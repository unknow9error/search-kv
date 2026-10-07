package kz.unknown.meken.ui

import androidx.activity.compose.BackHandler
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.runtime.saveable.rememberSaveableStateHolder
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import androidx.compose.ui.window.DialogProperties
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kz.unknown.meken.core.Apartment
import kz.unknown.meken.core.Preferences

@OptIn(ExperimentalMaterial3Api::class)
@Composable
fun MekenApp(model: MekenViewModel) {
    val state by model.state.collectAsStateWithLifecycle()
    val tabStateHolder = rememberSaveableStateHolder()
    var screen by rememberSaveable { mutableStateOf("search") }
    var filterPreferences by remember { mutableStateOf<Preferences?>(null) }
    var showFilters by rememberSaveable { mutableStateOf(false) }
    var showHelp by rememberSaveable { mutableStateOf(false) }
    var showHistory by rememberSaveable { mutableStateOf(false) }
    var selectedId by rememberSaveable { mutableStateOf<String?>(null) }
    var selectedSnapshot by remember { mutableStateOf<Apartment?>(null) }
    var confirmingReset by rememberSaveable { mutableStateOf(false) }
    var deletingConversationId by rememberSaveable { mutableStateOf<String?>(null) }
    var showOfflineSaved by rememberSaveable { mutableStateOf(false) }
    var restoringAccess by remember { mutableStateOf(false) }
    val selected = (state.search.apartments + state.favorites).firstOrNull { it.id == selectedId }
        ?: selectedSnapshot?.takeIf { it.id == selectedId }
    LaunchedEffect(Unit) { model.initialize() }
    BackHandler(enabled = screen != "search" && !showFilters && !showHistory && selectedId == null) { if (!state.isDeleting && !state.isRecovering) screen = "search" }

    if (!state.isReady) {
        Surface(Modifier.fillMaxSize(), color = MaterialTheme.colorScheme.background) {
            Column(Modifier.fillMaxSize().safeDrawingPadding().padding(28.dp), verticalArrangement = Arrangement.Center, horizontalAlignment = Alignment.CenterHorizontally) {
                BrandMark(Modifier.size(48.dp))
                Spacer(Modifier.height(20.dp))
                Text("meken", style = MaterialTheme.typography.headlineLarge)
                Spacer(Modifier.height(12.dp))
                if (state.isStarting || state.isRecovering) {
                    CircularProgressIndicator()
                    Spacer(Modifier.height(12.dp))
                    Text(if (state.isRecovering) "Восстанавливаю доступ" else "Подключаюсь к каталогу", style = MaterialTheme.typography.bodyMedium)
                } else {
                    (state.search.error ?: state.initializationError)?.let { NoticeCard(it) }
                    Spacer(Modifier.height(12.dp))
                    Button(onClick = model::initialize) { Text("Повторить подключение") }
                    if (state.favorites.isNotEmpty() && !state.sessionExpired) TextButton(onClick = { showOfflineSaved = true }) { Text("Открыть сохранённые квартиры") }
                    if (state.config == null || state.config?.capabilities?.contains("account_recovery") == true) TextButton(onClick = { restoringAccess = true }) { Text("Восстановить доступ по коду") }
                    if (state.sessionExpired) {
                        Spacer(Modifier.height(12.dp))
                        Text("Доступ на этом устройстве истёк. Если вы сохранили код доступа, восстановите подборки по нему. Можно также начать заново.", style = MaterialTheme.typography.bodyMedium)
                        TextButton(onClick = { confirmingReset = true }) { Text("Начать заново") }
                    }
                }
            }
        }
        if (confirmingReset) AlertDialog(onDismissRequest = { confirmingReset = false }, title = { Text("Начать заново?") },
            text = { Text("Начнётся новый подбор. Прежняя история и избранное перестанут отображаться на этом устройстве. Сохранённые на устройстве квартиры будут очищены.") },
            confirmButton = { TextButton(onClick = { confirmingReset = false; showOfflineSaved = false; selectedId = null; selectedSnapshot = null; model.resetSession() }) { Text("Начать заново") } },
            dismissButton = { TextButton(onClick = { confirmingReset = false }) { Text("Отмена") } })
        if (showOfflineSaved && !state.sessionExpired) FullScreenPage("Сохранено на устройстве", onClose = { showOfflineSaved = false; selectedId = null }) {
            SavedScreen(state, model, onOpen = { selectedId = it.id; selectedSnapshot = it })
        }
        if (showOfflineSaved && selected != null && !state.sessionExpired) FullScreenPage("Квартира", onClose = { selectedId = null }) {
            ApartmentDetail(selected, searchBusy = true, model = model, onDiscuss = {}, onRefresh = {}, canVerify = false)
        }
        if (restoringAccess) RestoreAccessDialog(model, state.isRecovering, onClose = { restoringAccess = false }, onRecovered = { showOfflineSaved = false; selectedId = null; selectedSnapshot = null; screen = "search" })
        return
    }

    Scaffold(
        modifier = Modifier.fillMaxSize().imePadding(),
        containerColor = MaterialTheme.colorScheme.background,
        topBar = {
            TopAppBar(title = {
                if (screen == "search") Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(9.dp)) {
                    Text("meken", fontWeight = FontWeight.Bold, style = MaterialTheme.typography.headlineSmall)
                } else Text(if (screen == "saved") "Избранное" else "Профиль")
            }, actions = {
                if (screen == "search") {
                    IconButton(onClick = { model.refreshHistory(); showHistory = true }) { Icon(Icons.Outlined.History, "История подборок") }
                    IconButton(onClick = model::newConversation, enabled = !state.search.isStreaming) { Icon(Icons.Outlined.Edit, "Новый подбор") }
                }
                if (screen == "saved") IconButton(onClick = model::loadFavorites) { Icon(Icons.Outlined.Refresh, "Обновить избранное") }
            }, colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background))
        },
        bottomBar = {
            NavigationBar(containerColor = MaterialTheme.colorScheme.surface, tonalElevation = 0.dp) {
                NavigationBarItem(selected = screen == "search", enabled = !state.isDeleting && !state.isRecovering, onClick = { screen = "search" }, icon = { Icon(Icons.Outlined.Search, null) }, label = { Text("Поиск") })
                NavigationBarItem(selected = screen == "saved", enabled = !state.isDeleting && !state.isRecovering, onClick = { screen = "saved"; model.loadFavorites() }, icon = { Icon(Icons.Outlined.FavoriteBorder, null) }, label = { Text("Избранное") })
                NavigationBarItem(selected = screen == "settings", enabled = !state.isDeleting && !state.isRecovering, onClick = { screen = "settings" }, icon = { Icon(Icons.Outlined.PersonOutline, null) }, label = { Text("Профиль") })
            }
        },
    ) { padding ->
        Box(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) {
            tabStateHolder.SaveableStateProvider(screen) {
                when (screen) {
                    "saved" -> SavedScreen(state, model, onOpen = { selectedId = it.id; selectedSnapshot = it })
                    "settings" -> SettingsScreen(state, model, onRecovered = { selectedId = null; selectedSnapshot = null; screen = "search" }, onSaved = { screen = "saved"; model.loadFavorites() }, onHistory = { model.refreshHistory(); showHistory = true })
                    else -> SearchScreen(state, model, onFilters = { city -> filterPreferences = state.search.preferences.copy(city = city); showFilters = true }, onOpen = { selectedId = it.id; selectedSnapshot = it }, onAbout = { showHelp = true })
                }
            }
        }
    }
    if (showFilters) FullScreenPage("Фильтры", onClose = { showFilters = false }) {
        FiltersScreen(filterPreferences ?: state.search.preferences, state.config?.cities.orEmpty(), state.search.isStreaming || state.search.isPending,
            onApply = { model.applyFilters(it); showFilters = false }, onClose = { showFilters = false })
    }
    if (showHelp) FullScreenPage("Помощь и документы", onClose = { showHelp = false }) { HelpScreen(state) }
    if (showHistory) FullScreenPage("История", onClose = { showHistory = false }, actions = {
        IconButton(onClick = model::refreshHistory) { Icon(Icons.Outlined.Refresh, "Обновить историю") }
    }) {
        LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(22.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            state.historyError?.let { error -> item { NoticeCard(error) } }
            if (state.conversations.isEmpty()) item { EmptyState("Здесь будут ваши подборки", "Начните разговор о квартире — он сохранится автоматически.") }
            items(state.conversations, key = { it.id }) { conversation ->
                Card(onClick = { model.restoreConversation(conversation.id); showHistory = false; screen = "search" }, enabled = conversation.id !in state.busyConversations) {
                    Row(Modifier.fillMaxWidth().padding(18.dp), verticalAlignment = Alignment.CenterVertically) {
                        Column(Modifier.weight(1f), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                            Text(conversation.title, style = MaterialTheme.typography.titleMedium)
                            Text(conversation.preferences.city ?: "Город ещё не выбран", style = MaterialTheme.typography.bodySmall)
                        }
                        IconButton(onClick = { deletingConversationId = conversation.id }, enabled = conversation.id !in state.busyConversations) { Icon(Icons.Outlined.DeleteOutline, "Удалить подборку ${conversation.title}") }
                    }
                }
            }
        }
    }
    if (deletingConversationId != null) AlertDialog(onDismissRequest = { deletingConversationId = null }, title = { Text("Удалить подборку?") },
        text = { Text("Сообщения и пожелания этой подборки будут удалены. Восстановить их не получится.") },
        confirmButton = { TextButton(onClick = { deletingConversationId?.let(model::deleteConversation); deletingConversationId = null }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Удалить") } },
        dismissButton = { TextButton(onClick = { deletingConversationId = null }) { Text("Отмена") } })
    if (selected != null) FullScreenPage("Квартира", onClose = { selectedId = null }, actions = {
        val favorite = state.favorites.any { it.id == selected.id }
        IconButton(onClick = { model.toggleFavorite(selected) }, enabled = selected.id !in state.busyFavorites) {
            Icon(if (favorite) Icons.Outlined.Favorite else Icons.Outlined.FavoriteBorder, if (favorite) "Убрать из избранного" else "Сохранить квартиру")
        }
    }) { ApartmentDetail(selected, state.search.isStreaming || state.search.isPending, model, onDiscuss = { selectedId = null; screen = "search"; model.explain(it) }, onRefresh = { selectedSnapshot = it }) }
}

@OptIn(ExperimentalMaterial3Api::class)
@Composable
internal fun FullScreenPage(title: String, onClose: () -> Unit, actions: @Composable RowScope.() -> Unit = {}, content: @Composable () -> Unit) {
    Dialog(onDismissRequest = onClose, properties = DialogProperties(usePlatformDefaultWidth = false, decorFitsSystemWindows = false)) {
        Scaffold(
            Modifier.fillMaxSize().imePadding(), containerColor = MaterialTheme.colorScheme.background,
            topBar = { TopAppBar(title = { Text(title) }, navigationIcon = { IconButton(onClick = onClose) { Icon(Icons.Outlined.Close, "Закрыть") } }, actions = actions,
                colors = TopAppBarDefaults.topAppBarColors(containerColor = MaterialTheme.colorScheme.background)) },
        ) { padding -> Box(Modifier.fillMaxSize().padding(padding).consumeWindowInsets(padding)) { content() } }
    }
}

@Composable
private fun SavedScreen(state: MekenUiState, model: MekenViewModel, onOpen: (Apartment) -> Unit) {
    LazyColumn(Modifier.fillMaxSize(), contentPadding = PaddingValues(22.dp), verticalArrangement = Arrangement.spacedBy(18.dp)) {
        if (state.favoritesOffline) item { NoticeCard("Показаны сохранённые на этом устройстве квартиры. Подключитесь к сети, чтобы обновить цену и наличие.") }
        state.favoriteError?.let { error -> item { NoticeCard(error) } }
        if (state.favorites.isEmpty()) item { EmptyState("Квартиры, к которым хочется вернуться", "Нажмите на сердечко в подборке. Сохранённые варианты появятся здесь.") }
        items(state.favorites, key = { it.id }) { apartment -> ApartmentCard(apartment, true, !state.isReady || apartment.id in state.busyFavorites, onOpen = { onOpen(apartment) }, onFavorite = { model.toggleFavorite(apartment) }) }
    }
}
