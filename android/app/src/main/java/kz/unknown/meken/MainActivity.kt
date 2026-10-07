package kz.unknown.meken

import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.SystemBarStyle
import androidx.activity.enableEdgeToEdge
import androidx.activity.viewModels
import kz.unknown.meken.ui.catalog.CatalogApp
import kz.unknown.meken.ui.catalog.CatalogViewModel
import kz.unknown.meken.ui.MekenTheme
import kz.unknown.meken.ui.MekenViewModel

class MainActivity : ComponentActivity() {
    private val model: MekenViewModel by viewModels {
        MekenViewModel.factory(applicationContext, BuildConfig.MEKEN_API_BASE_URL, allowInsecureLocalDebug = BuildConfig.DEBUG)
    }

    private val catalog: CatalogViewModel by viewModels { CatalogViewModel.factory(requireNotNull(model.catalogRepository)) }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        enableEdgeToEdge(
            statusBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
            navigationBarStyle = SystemBarStyle.light(android.graphics.Color.TRANSPARENT, android.graphics.Color.TRANSPARENT),
        )
        setContent { MekenTheme { CatalogApp(catalog, model, intent.getBooleanExtra("catalogOnboarding", false)) } }
    }
}
