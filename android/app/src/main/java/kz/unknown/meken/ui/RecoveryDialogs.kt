package kz.unknown.meken.ui

import android.content.ClipData
import android.content.ClipDescription
import android.content.ClipboardManager
import android.content.Context
import android.os.Build
import android.os.PersistableBundle
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalFocusManager
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.DialogProperties
import androidx.compose.ui.window.SecureFlagPolicy
import androidx.lifecycle.Lifecycle
import androidx.lifecycle.LifecycleEventObserver
import androidx.lifecycle.compose.LocalLifecycleOwner

/** The one-time code stays only in this dialog's memory and disappears on background/close. */
@Composable
internal fun RecoveryCodeDialog(model: MekenViewModel, generatingOnServer: Boolean, onClose: () -> Unit) {
    var code by remember { mutableStateOf<String?>(null) }
    var error by remember { mutableStateOf<String?>(null) }
    var generating by remember { mutableStateOf(false) }
    var active by remember { mutableStateOf(true) }
    val context = LocalContext.current
    HideSensitiveDialogOnStop(onClose = { code = null; active = false; onClose() })
    DisposableEffect(Unit) { onDispose { active = false; code = null } }
    AlertDialog(
        onDismissRequest = { code = null; active = false; onClose() },
        properties = DialogProperties(securePolicy = SecureFlagPolicy.SecureOn),
        title = { Text(if (code == null) "Создать код доступа?" else "Сохраните код доступа") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                Text("С этим кодом можно открыть свои подборки на другом устройстве или восстановить доступ.")
                Text("Любой, кто получит код, сможет открыть ваши диалоги и избранное. Храните его в надёжном месте и не отправляйте посторонним.")
                if (code == null) Text("Новый код заменит предыдущий. Предыдущий код больше не будет работать.")
                else {
                    Text(code.orEmpty(), fontFamily = FontFamily.Monospace, style = MaterialTheme.typography.bodyLarge)
                    Text("После закрытия приложение больше не покажет этот код. Скопируйте или запишите его.", style = MaterialTheme.typography.bodySmall)
                }
                if ((generating || generatingOnServer) && code == null) { CircularProgressIndicator(Modifier.size(24.dp)); Text("Создаю код доступа") }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            if (code != null) TextButton(onClick = { code?.let { copyRecoveryCode(context, it) } }) { Text("Скопировать") }
            else TextButton(onClick = {
                if (generating || generatingOnServer) return@TextButton
                generating = true; error = null
                model.generateRecoveryCode { generated, failure ->
                    if (active) { generating = false; code = generated; error = failure }
                }
            }, enabled = !generating && !generatingOnServer) { Text("Создать код") }
        },
        dismissButton = { TextButton(onClick = { code = null; active = false; onClose() }) { Text(if (code == null && !generating) "Отмена" else "Закрыть") } },
    )
}

@Composable
internal fun RestoreAccessDialog(model: MekenViewModel, recovering: Boolean, onClose: () -> Unit, onRecovered: () -> Unit) {
    var draft by remember { mutableStateOf("") }
    var error by remember { mutableStateOf<String?>(null) }
    var confirming by remember { mutableStateOf(false) }
    var active by remember { mutableStateOf(true) }
    HideSensitiveDialogOnStop(onClose = { draft = ""; active = false; onClose() })
    DisposableEffect(Unit) { onDispose { active = false; draft = "" } }
    AlertDialog(
        onDismissRequest = { if (!recovering) { draft = ""; active = false; onClose() } },
        properties = DialogProperties(securePolicy = SecureFlagPolicy.SecureOn, dismissOnBackPress = !recovering, dismissOnClickOutside = !recovering),
        title = { Text(if (confirming) "Открыть подборки по коду?" else "Восстановить доступ") },
        text = {
            Column(Modifier.verticalScroll(rememberScrollState()), verticalArrangement = Arrangement.spacedBy(14.dp)) {
                if (confirming) Text("На этом устройстве откроются подборки, связанные с кодом. Текущие история и избранное будут заменены на этом устройстве. Они останутся в сервисе и не будут объединены с восстановленными.")
                else {
                    Text("Введите сохранённый код доступа. Для восстановления нужен интернет.")
                    OutlinedTextField(
                        value = draft,
                        onValueChange = { value -> draft = if (value.codePointCount(0, value.length) <= 80) value else value.substring(0, value.offsetByCodePoints(0, 80)) },
                        label = { Text("Код доступа") }, modifier = Modifier.fillMaxWidth(), singleLine = true,
                        visualTransformation = PasswordVisualTransformation(),
                        keyboardOptions = KeyboardOptions(autoCorrectEnabled = false, keyboardType = KeyboardType.Password),
                    )
                }
                if (recovering) { CircularProgressIndicator(Modifier.size(24.dp)); Text("Восстанавливаю доступ") }
                error?.let { Text(it, color = MaterialTheme.colorScheme.error) }
            }
        },
        confirmButton = {
            val focus = LocalFocusManager.current
            TextButton(onClick = {
                error = null
                if (!confirming) { focus.clearFocus(); confirming = true }
                else model.recoverAccount(draft.trim()) { failure ->
                    if (active) {
                        if (failure == null) { draft = ""; active = false; onClose(); onRecovered() }
                        else { error = failure; confirming = false }
                    }
                }
            }, enabled = !recovering && draft.isNotBlank()) { Text(if (confirming) "Открыть подборки" else "Продолжить") }
        },
        dismissButton = { TextButton(onClick = { draft = ""; active = false; onClose() }, enabled = !recovering) { Text("Отмена") } },
    )
}

@Composable
private fun HideSensitiveDialogOnStop(onClose: () -> Unit) {
    val owner = LocalLifecycleOwner.current
    val latestClose by rememberUpdatedState(onClose)
    DisposableEffect(owner) {
        val observer = LifecycleEventObserver { _, event -> if (event == Lifecycle.Event.ON_STOP) latestClose() }
        owner.lifecycle.addObserver(observer)
        onDispose { owner.lifecycle.removeObserver(observer) }
    }
}

private fun copyRecoveryCode(context: Context, code: String) {
    val clip = ClipData.newPlainText("Код доступа Meken", code)
    clip.description.extras = PersistableBundle().apply {
        putBoolean(if (Build.VERSION.SDK_INT >= 33) ClipDescription.EXTRA_IS_SENSITIVE else "android.content.extra.IS_SENSITIVE", true)
    }
    val copied = runCatching {
        val clipboard = context.getSystemService(ClipboardManager::class.java) ?: return@runCatching false
        clipboard.setPrimaryClip(clip)
        true
    }.getOrDefault(false)
    android.widget.Toast.makeText(context, if (copied) "Код скопирован" else "Не удалось скопировать код. Запишите его вручную.", android.widget.Toast.LENGTH_SHORT).show()
}
