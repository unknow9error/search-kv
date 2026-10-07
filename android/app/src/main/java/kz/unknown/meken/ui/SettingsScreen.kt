package kz.unknown.meken.ui

import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import kz.unknown.meken.BuildConfig
import kz.unknown.meken.core.safeHttpsUrl

@Composable
internal fun SettingsScreen(state: MekenUiState, model: MekenViewModel, onRecovered: () -> Unit, onSaved: () -> Unit, onHistory: () -> Unit) {
    var confirmingDelete by rememberSaveable { mutableStateOf(false) }
    var deletionRequested by rememberSaveable { mutableStateOf(false) }
    var showingHelp by rememberSaveable { mutableStateOf(false) }
    var showingCode by remember { mutableStateOf(false) }
    var restoringAccess by remember { mutableStateOf(false) }
    var recoveryAttempted by remember { mutableStateOf(false) }
    val recoverySupported = state.config?.capabilities?.contains("account_recovery") == true
    LaunchedEffect(state.isRecovering) { if (state.isRecovering) recoveryAttempted = true }
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        NoticeCard("Подборки и избранное доступны без регистрации.")
        ProfileGroup("Ваши данные") {
            ProfileRow("Избранное", Icons.Outlined.FavoriteBorder, onSaved)
            HorizontalDivider(color = MaterialTheme.colorScheme.outlineVariant)
            ProfileRow("История поиска", Icons.Outlined.History, onHistory)
        }
        if (recoverySupported) ProfileGroup("На другом устройстве") {
            Text("Сохраните код, чтобы открыть этот профиль на другом устройстве.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            Button(onClick = { showingCode = true }, enabled = !state.isRecovering && !state.isDeleting,
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(8.dp)) { Text("Создать код восстановления") }
            OutlinedButton(onClick = { restoringAccess = true }, enabled = !state.isRecovering && !state.isDeleting,
                modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(8.dp)) { Text("Восстановить по коду") }
            Text("Код даёт доступ к подборкам и избранному. Храните его в надёжном месте.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        }
        ProfileGroup {
            ProfileRow("Помощь и документы", Icons.Outlined.Description) { showingHelp = true }
        }
        TextButton(onClick = { confirmingDelete = true }, enabled = !state.isDeleting && !state.isRecovering, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Удалить мои данные") }
        if (state.isDeleting) Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp); Text("Удаляю данные", style = MaterialTheme.typography.bodyMedium)
        }
        if (state.isRecovering) Row(horizontalArrangement = Arrangement.spacedBy(12.dp), verticalAlignment = Alignment.CenterVertically) {
            CircularProgressIndicator(Modifier.size(22.dp), strokeWidth = 2.dp); Text("Восстанавливаю доступ", style = MaterialTheme.typography.bodyMedium)
        }
        if ((deletionRequested || recoveryAttempted) && !state.isRecovering) state.search.error?.let { NoticeCard(it) }
        Text("Будут удалены диалоги, пожелания, избранное и текущая сессия. После этого можно начать заново.", style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
        Text("Версия ${BuildConfig.VERSION_NAME} (${BuildConfig.VERSION_CODE})", style = MaterialTheme.typography.bodySmall)
    }
    if (showingHelp) FullScreenPage("Помощь и документы", onClose = { showingHelp = false }) { HelpScreen(state) }
    if (showingCode) RecoveryCodeDialog(model, state.isGeneratingRecoveryCode, onClose = { showingCode = false })
    if (restoringAccess) RestoreAccessDialog(model, state.isRecovering, onClose = { restoringAccess = false }, onRecovered = onRecovered)
    if (confirmingDelete) AlertDialog(onDismissRequest = { confirmingDelete = false }, title = { Text("Удалить все мои данные?") }, text = { Text("Диалоги, пожелания и избранное будут удалены с сервера и этого устройства. Восстановить их не получится.") },
        confirmButton = { TextButton(onClick = { confirmingDelete = false; deletionRequested = true; model.deleteAccount() }, colors = ButtonDefaults.textButtonColors(contentColor = MaterialTheme.colorScheme.error)) { Text("Удалить") } },
        dismissButton = { TextButton(onClick = { confirmingDelete = false }) { Text("Отмена") } })
}

@Composable
private fun ProfileGroup(title: String? = null, content: @Composable ColumnScope.() -> Unit) {
    Surface(color = MaterialTheme.colorScheme.surfaceVariant, contentColor = MaterialTheme.colorScheme.onSurface, shape = RoundedCornerShape(12.dp)) {
        Column(Modifier.fillMaxWidth().padding(16.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
            title?.let { Text(it, style = MaterialTheme.typography.titleSmall) }
            content()
        }
    }
}

@Composable
private fun ProfileRow(title: String, icon: androidx.compose.ui.graphics.vector.ImageVector, onClick: () -> Unit) {
    TextButton(onClick = onClick, modifier = Modifier.fillMaxWidth(), contentPadding = PaddingValues(0.dp)) {
        Icon(icon, null, Modifier.size(20.dp)); Spacer(Modifier.width(10.dp))
        Text(title, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
        Icon(Icons.Outlined.ChevronRight, null, Modifier.size(18.dp))
    }
}

@Composable
internal fun HelpScreen(state: MekenUiState) {
    val context = LocalContext.current
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(22.dp), verticalArrangement = Arrangement.spacedBy(20.dp)) {
        Text("Ищите квартиры по городу, бюджету и количеству комнат. Сохраните понравившиеся варианты или уточните пожелания в разговоре.", style = MaterialTheme.typography.bodyMedium)
        ProfileGroup("О каталоге") {
            if (state.config?.aiEnabled != true) Text("Сейчас включён базовый подбор: он понимает простые условия. Для сложных пожеланий используйте фильтры.", style = MaterialTheme.typography.bodySmall)
            HelpAnswer("Что означают источник и дата?", "Источник ведёт к сведениям застройщика. Дата в карточке показывает, когда приложение получило эти сведения.")
            HelpAnswer("Можно ли забронировать квартиру?", "Проверка в карточке запрашивает новый ответ, но не бронирует квартиру. Наличие и цена могут измениться. Для покупки откройте первоисточник.")
            HelpAnswer("Почему в карточке нет сведений?", "В подключённом каталоге могут отсутствовать изображение, инфраструктура или другие сведения. Отсутствие данных не означает отсутствие объекта.")
        }
        ProfileGroup("Документы и данные") {
            safeHttpsUrl(state.config?.privacyUrl)?.let { url -> ProfileRow("Политика конфиденциальности", Icons.Outlined.Description) { openHttps(context, url) } }
            safeHttpsUrl(state.config?.termsUrl)?.let { url -> ProfileRow("Условия использования", Icons.Outlined.Description) { openHttps(context, url) } }
            Text("Сообщения и пожелания хранятся на сервере до ${state.config?.retentionDays ?: 90} дней с последней активности. Если ИИ включён, текст запросов передаётся поставщику ИИ. Не отправляйте ИИН, документы или платёжные данные.", style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
private fun HelpAnswer(question: String, answer: String) {
    var expanded by rememberSaveable { mutableStateOf(false) }
    TextButton(onClick = { expanded = !expanded }, modifier = Modifier.fillMaxWidth(), contentPadding = PaddingValues(0.dp)) {
        Text(question, Modifier.weight(1f), style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurface)
        Icon(if (expanded) Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore, null, Modifier.size(18.dp))
    }
    if (expanded) Text(answer, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
}
