package kz.unknown.meken.ui

import android.content.ActivityNotFoundException
import android.content.Context
import android.content.Intent
import androidx.core.net.toUri
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.Home
import androidx.compose.material.icons.outlined.Info
import androidx.compose.material3.*
import androidx.compose.runtime.Composable
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import kz.unknown.meken.core.safeHttpsUrl

private val LightColors = lightColorScheme(
    primary = Color(0xFF20564C), onPrimary = Color.White,
    primaryContainer = Color(0xFFE7EEE8), onPrimaryContainer = Color(0xFF20564C),
    background = Color.White, onBackground = Color(0xFF1E2C27),
    surface = Color.White, onSurface = Color(0xFF1E2C27),
    surfaceVariant = Color(0xFFF4F6F5), onSurfaceVariant = Color(0xFF617068),
    surfaceContainer = Color.White, surfaceContainerLow = Color.White,
    surfaceContainerLowest = Color.White, surfaceContainerHigh = Color(0xFFF4F6F5),
    surfaceContainerHighest = Color(0xFFF4F6F5),
    secondary = Color(0xFF20564C), onSecondary = Color.White,
    secondaryContainer = Color(0xFFE7EEE8), onSecondaryContainer = Color(0xFF20564C),
    outline = Color(0xFFBDC8BD), outlineVariant = Color(0xFFE1E8E3), surfaceTint = Color.Transparent,
)

@Composable
fun MekenTheme(content: @Composable () -> Unit) {
    val defaults = Typography()
    MaterialTheme(
        // The approved mobile design uses a white canvas, including with system dark mode.
        colorScheme = LightColors,
        typography = defaults.copy(
            headlineLarge = defaults.headlineLarge.copy(fontWeight = FontWeight.Bold),
            headlineMedium = defaults.headlineMedium.copy(fontWeight = FontWeight.Bold),
            titleLarge = defaults.titleLarge.copy(fontWeight = FontWeight.Bold),
        ),
        shapes = Shapes(small = RoundedCornerShape(8.dp), medium = RoundedCornerShape(10.dp), large = RoundedCornerShape(12.dp)),
        content = content,
    )
}

@Composable
internal fun BrandMark(modifier: Modifier = Modifier) {
    Surface(modifier.size(34.dp), color = MaterialTheme.colorScheme.primary, shape = RoundedCornerShape(10.dp)) {
        Icon(Icons.Outlined.Home, contentDescription = null, tint = MaterialTheme.colorScheme.onPrimary, modifier = Modifier.padding(7.dp))
    }
}

@Composable
internal fun NoticeCard(text: String, modifier: Modifier = Modifier) {
    Surface(modifier.fillMaxWidth(), color = MaterialTheme.colorScheme.primaryContainer, shape = RoundedCornerShape(10.dp)) {
        Row(Modifier.padding(14.dp), horizontalArrangement = Arrangement.spacedBy(10.dp)) {
            Icon(Icons.Outlined.Info, contentDescription = null, modifier = Modifier.size(20.dp))
            Text(text, style = MaterialTheme.typography.bodySmall)
        }
    }
}

@Composable
internal fun EmptyState(title: String, detail: String, modifier: Modifier = Modifier) {
    Column(modifier.fillMaxWidth().padding(vertical = 42.dp), verticalArrangement = Arrangement.spacedBy(12.dp)) {
        Text(title, style = MaterialTheme.typography.headlineMedium)
        Text(detail, style = MaterialTheme.typography.bodyMedium, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
}

/** Never pass arbitrary server links to an Android intent. */
internal fun openHttps(context: Context, raw: String?) {
    val url = safeHttpsUrl(raw) ?: return
    openIntent(context, Intent(Intent.ACTION_VIEW, url.toUri()))
}

internal fun openIntent(context: Context, intent: Intent) {
    try {
        context.startActivity(intent)
    } catch (_: ActivityNotFoundException) {
        android.widget.Toast.makeText(context, "Нет приложения для открытия ссылки.", android.widget.Toast.LENGTH_SHORT).show()
    } catch (_: SecurityException) {
        android.widget.Toast.makeText(context, "Не удалось открыть ссылку.", android.widget.Toast.LENGTH_SHORT).show()
    }
}
