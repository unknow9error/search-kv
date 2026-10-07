package kz.unknown.meken.ui.catalog

import androidx.activity.compose.BackHandler
import androidx.compose.animation.core.CubicBezierEasing
import androidx.compose.animation.core.animateFloatAsState
import androidx.compose.animation.core.tween
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.*
import androidx.compose.ui.*
import androidx.compose.ui.draw.clipToBounds
import androidx.compose.ui.graphics.graphicsLayer
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.lifecycle.compose.collectAsStateWithLifecycle
import kz.unknown.meken.R
import kz.unknown.meken.core.catalog.*
import kz.unknown.meken.ui.*

@Composable internal fun CatalogApp(model:CatalogViewModel,session:MekenViewModel,forceOnboarding:Boolean=false){
    val state by model.state.collectAsStateWithLifecycle();val account by session.state.collectAsStateWithLifecycle()
    val context=LocalContext.current;val preferences=remember{context.getSharedPreferences("catalog-ui",0)}
    var onboarded by rememberSaveable{mutableStateOf(if(forceOnboarding)false else preferences.getBoolean("onboarded",false))}
    var recovering by remember{mutableStateOf(false)}
    val holder=key(state.scope){rememberSaveableStateHolder()}
    LaunchedEffect(Unit){session.initializeCatalogSession();model.initialize()}
    BackHandler(onboarded&&state.paths[state.tab].size>1){model.back()}
    Surface(Modifier.fillMaxSize().imePadding(),color=MaterialTheme.colorScheme.background){
        Column(Modifier.fillMaxSize().safeDrawingPadding()){
            if(!onboarded) Onboarding(state,model){onboarded=true;preferences.edit().putBoolean("onboarded",true).apply()}
            else {
                state.error?.let{error->Row(Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.primaryContainer).padding(10.dp),verticalAlignment=Alignment.CenterVertically){Text(error,Modifier.weight(1f),style=MaterialTheme.typography.bodySmall);TextButton(onClick=model::initialize){Text("Повторить")}}}
                if(state.offline) Text("Сохранённые сведения. Подключитесь для нового поиска.",style=MaterialTheme.typography.bodySmall,modifier=Modifier.padding(8.dp))
                if(state.ready&&!state.supported&&state.page in listOf(CatalogPage.PROFILE,CatalogPage.HELP)) Text("Каталог ЖК пока недоступен на подключённом сервере.",style=MaterialTheme.typography.bodySmall,modifier=Modifier.fillMaxWidth().background(MaterialTheme.colorScheme.primaryContainer).padding(12.dp))
                Box(Modifier.weight(1f).fillMaxWidth()){
                    holder.SaveableStateProvider(state.route.key){
                    if(state.ready&&!state.supported&&state.page !in listOf(CatalogPage.PROFILE,CatalogPage.HELP)) CatalogUnavailable(state,model)
                    else when(state.page){
                        CatalogPage.SEARCH->Search(state,model)
                        CatalogPage.FILTERS->Filters(state,model)
                        CatalogPage.RESULTS->Results(state,model)
                        CatalogPage.MAP->MapScreen(state,model)
                        CatalogPage.CONVERSATION->Conversation(state,model)
                        CatalogPage.PROJECT->ProjectScreen(state,model)
                        CatalogPage.LAYOUT->LayoutScreen(state,model)
                        CatalogPage.COMPARISON->Comparison(state,model)
                        CatalogPage.FAVORITES->Favorites(state,model)
                        CatalogPage.HISTORY->History(state,model)
                        CatalogPage.PROFILE->Profile(state,model,session,account){recovering=true}
                        CatalogPage.HELP->Help(state,model)
                        CatalogPage.SOURCES->Sources(state,model)
                        CatalogPage.REPORT->Report(state,model)
                    }}
                }
                if(state.tabsVisible){HorizontalDivider(color=MaterialTheme.colorScheme.outlineVariant);Row(Modifier.fillMaxWidth().heightIn(min=56.dp),horizontalArrangement=Arrangement.SpaceEvenly){
                    listOf("Поиск" to Icons.Outlined.Search,"Избранное" to Icons.Outlined.FavoriteBorder,"Профиль" to Icons.Outlined.PersonOutline).forEachIndexed{i,(label,icon)->
                        TextButton(onClick={model.tab(i);if(i==1)model.favorites()},modifier=Modifier.weight(1f),contentPadding=PaddingValues(vertical=6.dp)){
                            Column(horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(3.dp)){Icon(icon,label,Modifier.size(21.dp),tint=if(state.tab==i)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant);Text(label,style=MaterialTheme.typography.labelSmall,color=if(state.tab==i)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.onSurfaceVariant);Box(Modifier.width(42.dp).height(2.dp).background(if(state.tab==i)MaterialTheme.colorScheme.primary else androidx.compose.ui.graphics.Color.Transparent))}
                        }
                    }
                }}
            }
        }
    }
    if(recovering)RestoreAccessDialog(session,account.isRecovering,onClose={recovering=false},onRecovered={model.clearScope();model.initialize()})
}
@Composable private fun CatalogUnavailable(state:CatalogState,model:CatalogViewModel) {
    Column(verticalArrangement=Arrangement.spacedBy(20.dp)) {
        Header(if(state.page==CatalogPage.FAVORITES)"Избранное" else "Каталог ЖК",back=if(state.tabsVisible)null else model::back)
        Text("Каталог ЖК пока недоступен на подключённом сервере.",style=MaterialTheme.typography.bodyMedium,modifier=Modifier.padding(horizontal=16.dp))
        Box(Modifier.padding(horizontal=16.dp)) { Primary("Повторить подключение",!state.busy,model::initialize) }
    }
}
@Composable private fun Onboarding(state:CatalogState,model:CatalogViewModel,onBegin:()->Unit){
    var revealed by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { revealed = true }
    val heroProgress by animateFloatAsState(
        targetValue = if (revealed) 1f else 0f,
        animationSpec = tween(900, easing = OnboardingEasing),
        label = "welcome-photo"
    )
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())){
        Box(Modifier.fillMaxWidth().height(340.dp).clipToBounds()) {
            ReferenceRegion(R.drawable.catalogonboarding,0,0,852,888,Modifier.fillMaxSize().graphicsLayer {
                alpha = heroProgress
                scaleX = 1f + (1f - heroProgress) * 0.035f
                scaleY = scaleX
            })
        }
        Column(Modifier.padding(24.dp),verticalArrangement=Arrangement.spacedBy(20.dp)){
            OnboardingEntrance(revealed, 120) {
                Text("ЖК разных\nзастройщиков\nв одном месте",style=MaterialTheme.typography.headlineMedium,fontWeight=FontWeight.Bold)
            }
            OnboardingEntrance(revealed, 220) {
                Text("Ищите, сравнивайте и сохраняйте ЖК.\nСведения о ЖК — из открытых источников.",style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
            OnboardingEntrance(revealed, 320) {
                Column(verticalArrangement=Arrangement.spacedBy(20.dp)) {
                    Text("Город поиска",style=MaterialTheme.typography.bodyMedium)
                    ChoiceRow("",state.criteria.city ?: "Выберите город",state.config?.cities.orEmpty().map{it to it},model::city)
                }
            }
            OnboardingEntrance(revealed, 420) {
                Primary("Начать поиск",state.ready&&state.criteria.city!=null,onBegin)
            }
            if(state.busy)CircularProgressIndicator(Modifier.size(24.dp))
            if(state.config?.isDemo==true)Text("Демонстрационный каталог",style=MaterialTheme.typography.labelSmall)
            state.error?.let{Text(it,style=MaterialTheme.typography.bodySmall);TextButton(onClick=model::initialize){Text("Повторить")}}
        }
    }
}
private val OnboardingEasing = CubicBezierEasing(0f, 0f, 0.58f, 1f)

@Composable private fun OnboardingEntrance(revealed:Boolean,delayMillis:Int,content:@Composable ()->Unit) {
    val progress by animateFloatAsState(
        targetValue = if (revealed) 1f else 0f,
        animationSpec = tween(540, delayMillis, OnboardingEasing),
        label = "welcome-section-$delayMillis"
    )
    // Compose's animation clock follows the system animator duration scale, including zero.
    Box(Modifier.graphicsLayer {
        alpha = progress
        translationY = 16.dp.toPx() * (1f - progress)
    }) { content() }
}
@Composable private fun Search(state:CatalogState,model:CatalogViewModel){
    Column {
        Header{TextButton(onClick={model.push(CatalogPage.HISTORY);model.history()}){Icon(Icons.Outlined.History,null,Modifier.size(16.dp));Text("История",style=MaterialTheme.typography.labelSmall)}}
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(14.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
            Heading("Поиск ЖК")
            ChoiceRow("",state.criteria.city ?: "Выберите город",state.config?.cities.orEmpty().map{it to it},model::city)
            OutlinedTextField(state.criteria.q.orEmpty(),{model.criteria(state.criteria.copy(q=it.ifBlank{null}))},Modifier.fillMaxWidth(),placeholder={Text("Название ЖК или район",style=MaterialTheme.typography.bodyMedium)},leadingIcon={Icon(Icons.Outlined.Search,null,Modifier.size(20.dp))},singleLine=true,shape=androidx.compose.foundation.shape.RoundedCornerShape(9.dp))
            FieldRow("Цена ЖК «от»",state.criteria.priceSummary,Icons.Outlined.Payments){model.push(CatalogPage.FILTERS)}
            FieldRow("Район",state.criteria.districts.joinToString(", ").ifEmpty{"Все районы"},Icons.Outlined.Apartment){model.push(CatalogPage.FILTERS)}
            Row(horizontalArrangement=Arrangement.spacedBy(8.dp)){
                OutlinedButton(onClick={model.criteria(state.criteria.copy(stages=if("commissioned" in state.criteria.stages)emptyList() else listOf("commissioned")))},modifier=Modifier.weight(1f),contentPadding=PaddingValues(8.dp)){Text("Сданные ЖК",style=MaterialTheme.typography.labelSmall);Icon(if("commissioned" in state.criteria.stages)Icons.Outlined.CheckCircle else Icons.Outlined.RadioButtonUnchecked,null,Modifier.size(16.dp))}
                OutlinedButton(onClick={model.push(CatalogPage.CONVERSATION)},modifier=Modifier.weight(1f),contentPadding=PaddingValues(8.dp)){Icon(Icons.Outlined.School,null,Modifier.size(16.dp));Text("Школа рядом",style=MaterialTheme.typography.labelSmall)}
            }
            FieldRow("","Все фильтры",Icons.Outlined.Tune){model.push(CatalogPage.FILTERS)}
            Primary(if(state.busy)"Ищем ЖК" else "Найти ЖК",!state.busy&&state.supported&&!state.offline){model.search()}
            Surface(onClick={model.push(CatalogPage.CONVERSATION)},enabled=state.supported&&!state.offline,color=MaterialTheme.colorScheme.primaryContainer,shape=androidx.compose.foundation.shape.RoundedCornerShape(9.dp)){
                Row(Modifier.fillMaxWidth().padding(12.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)){Icon(Icons.Outlined.ChatBubbleOutline,null,Modifier.size(22.dp));Column(Modifier.weight(1f)){Text("Уточнить в разговоре",style=MaterialTheme.typography.bodyMedium);Text("Опишите, что важно для вас",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)};Icon(Icons.Outlined.ChevronRight,null,Modifier.size(16.dp))}
            }
            state.conversations.firstOrNull()?.let{recent->Text("Последний поиск",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant,modifier=Modifier.padding(top=10.dp));FieldRow("",recent.title,Icons.Outlined.History){model.restore(recent)}}
            TextButton(onClick={model.push(CatalogPage.HELP)},contentPadding=PaddingValues(0.dp)){Icon(Icons.Outlined.Info,null,Modifier.size(16.dp));Spacer(Modifier.width(7.dp));Text("О каталоге и источниках",style=MaterialTheme.typography.labelSmall)}
            if(state.config?.isDemo==true)Text("Демо-каталог. Это не реальные предложения.",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            if(state.ready&&!state.supported)Text("Каталог ЖК пока недоступен на подключённом сервере.",style=MaterialTheme.typography.bodySmall)
        }
    }
}
@Composable internal fun Results(state:CatalogState,model:CatalogViewModel){
    Column {
        LazyColumn(Modifier.weight(1f).fillMaxWidth(),contentPadding=PaddingValues(14.dp),verticalArrangement=Arrangement.spacedBy(13.dp)){
            item{Text(state.criteria.city ?: "Все города",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}
            item{Heading(if(state.criteria.districts.size==1)(if(state.criteria.districts[0]=="Есиль")"ЖК в Есиле" else "ЖК · ${state.criteria.districts[0]}") else "Жилые комплексы");Text("${state.total} ЖК",style=MaterialTheme.typography.bodySmall)}
            item{CriteriaChips(state,model)}
            item{Row(verticalAlignment=Alignment.CenterVertically){OutlinedButton(onClick={model.push(CatalogPage.FILTERS)},contentPadding=PaddingValues(8.dp)){Text("Фильтры",style=MaterialTheme.typography.labelSmall)};Spacer(Modifier.width(7.dp));var sorting by remember{mutableStateOf(false)};Box(Modifier.weight(1f)){OutlinedButton(onClick={sorting=true},contentPadding=PaddingValues(8.dp)){Text("По цене «от»",style=MaterialTheme.typography.labelSmall)};DropdownMenu(sorting,onDismissRequest={sorting=false}){listOf("price_asc" to "По цене «от»","price_desc" to "По цене ↓","name" to "По названию").forEach{(key,label)->DropdownMenuItem(text={Text(label)},onClick={sorting=false;model.search(sort=key)})}}};OutlinedButton(onClick={model.push(CatalogPage.MAP);model.map()},contentPadding=PaddingValues(8.dp)){Icon(Icons.Outlined.Map,null,Modifier.size(16.dp));Text("Карта",style=MaterialTheme.typography.labelSmall)}}}
            if(state.busy)item{CircularProgressIndicator(Modifier.size(24.dp))}
            if(state.results.isEmpty()&&!state.busy)item{Text("Нет ЖК по этим условиям. Измените фильтры.",style=MaterialTheme.typography.bodyMedium)}
            items(state.results,key={it.id}){ProjectCard(it,state,model)}
            if(state.cursor!=null)item{TextButton(onClick={model.search(more=true)},enabled=!state.busy){Text("Показать ещё")}}
        }
        if(state.selection.size>=2)Box(Modifier.padding(12.dp)){Primary("Сравнить ${state.selection.size} ЖК",!state.busy,model::compare)}
    }
}
