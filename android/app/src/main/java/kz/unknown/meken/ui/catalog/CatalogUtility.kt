package kz.unknown.meken.ui.catalog

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.*
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import java.util.UUID
import kz.unknown.meken.core.safeHttpsUrl
import kz.unknown.meken.core.catalog.*
import kz.unknown.meken.ui.*

@Composable internal fun Conversation(state:CatalogState,model:CatalogViewModel){
    var draft by rememberSaveable{mutableStateOf("")}
    Column {
        Header("Разговор",model::back)
        LazyColumn(Modifier.weight(1f).fillMaxWidth(),contentPadding=PaddingValues(14.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
            items(listOfNotNull(state.criteria.city,state.criteria.districts.firstOrNull(),state.criteria.priceMax?.let{state.criteria.priceModeLabel+" "+state.criteria.priceSummary.lowercase()}),key={it}){criterion->FieldRow("",criterion){model.push(CatalogPage.FILTERS)}}
            if(state.turns.isEmpty())item{Text("Уточните пожелания. Базовый помощник использует сведения каталога; сложные условия задайте через фильтры.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}
            if(state.historyCursor!=null)state.conversations.firstOrNull{it.id==state.conversationId}?.let{c->item{TextButton(onClick={model.restore(c,true)}){Text("Предыдущие сообщения")}}}
            items(state.turns,key={it.id}){turn->Column(verticalArrangement=Arrangement.spacedBy(14.dp)){
                Row(Modifier.fillMaxWidth(),horizontalArrangement=Arrangement.End){Surface(shape=androidx.compose.foundation.shape.RoundedCornerShape(12.dp),color=MaterialTheme.colorScheme.primaryContainer){Text(turn.message,Modifier.padding(12.dp),style=MaterialTheme.typography.bodyMedium)}}
                turn.response?.let{reply->Group{Text(reply.message,style=MaterialTheme.typography.bodyMedium)};reply.results?.items?.take(2)?.forEach{ProjectCard(it,state,model,compact=true)}} ?: Text("Ответ не завершён. Повторите запрос.",style=MaterialTheme.typography.bodySmall)
            }}
            state.pending?.let{pending->item{Group{Text(pending.message,style=MaterialTheme.typography.bodyMedium);TextButton(onClick={model.send(pending.message,true)},enabled=!state.busy){Text("Повторить отправку")}}}}
            if(state.busy)item{CircularProgressIndicator(Modifier.size(24.dp))}
            if(state.suggestions.isNotEmpty())item{Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)){state.suggestions.forEach{suggestion->Chip(suggestion){model.send(suggestion)}}}}
        }
        Row(Modifier.fillMaxWidth().padding(14.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)){
            OutlinedTextField(draft,{value->if(value.codePointCount(0,value.length)<=2000)draft=value},Modifier.weight(1f),placeholder={Text("Уточнить пожелания",style=MaterialTheme.typography.bodyMedium)},maxLines=5,shape=androidx.compose.foundation.shape.RoundedCornerShape(9.dp))
            FilledIconButton(onClick={val text=draft;draft="";model.send(text)},enabled=draft.isNotBlank()&&!state.busy&&state.pending==null){Icon(Icons.Outlined.NorthEast,"Отправить")}
        }
    }
}
@Composable internal fun History(state:CatalogState,model:CatalogViewModel){var deleting by remember{mutableStateOf<ProjectConversation?>(null)};Column{
    Header("История",model::back){TextButton(onClick=model::newSearch){Icon(Icons.Outlined.Add,null,Modifier.size(15.dp));Text("Новый поиск",style=MaterialTheme.typography.labelSmall)}}
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(16.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
        item{Text("Сохранённые поиски",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}
        if(state.conversations.isEmpty())item{Text("История пока пуста. Подборки из разговора сохраняются здесь.",style=MaterialTheme.typography.bodyMedium)}
        items(state.conversations,key={it.id}){c->Group{Row(verticalAlignment=Alignment.CenterVertically){TextButton(onClick={model.restore(c)},modifier=Modifier.weight(1f),contentPadding=PaddingValues(0.dp)){Icon(Icons.Outlined.Search,null,Modifier.size(20.dp));Spacer(Modifier.width(10.dp));Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(5.dp)){Text(c.title,style=MaterialTheme.typography.titleSmall,color=MaterialTheme.colorScheme.onSurface);Text("${c.criteria.city ?: "Все города"} · ${c.criteria.priceModeLabel} ${c.criteria.priceSummary.lowercase()}",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}};IconButton(onClick={deleting=c}){Icon(Icons.Outlined.MoreVert,"Управление поиском ${c.title}")}}}}
    }
};deleting?.let{c->AlertDialog(onDismissRequest={deleting=null},title={Text("Удалить поиск?")},text={Text("Сообщения и условия поиска будут удалены. Избранные ЖК сохранятся.")},confirmButton={TextButton(onClick={deleting=null;model.deleteConversation(c.id)}){Text("Удалить")}},dismissButton={TextButton(onClick={deleting=null}){Text("Отмена")}})} }
@Composable internal fun Profile(state:CatalogState,model:CatalogViewModel,session:MekenViewModel,account:MekenUiState,onRecover:()->Unit){
    var code by remember{mutableStateOf(false)};var confirmingDelete by rememberSaveable{mutableStateOf(false)}
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),verticalArrangement=Arrangement.spacedBy(16.dp)){
        Heading("Профиль")
        Surface(color=MaterialTheme.colorScheme.primaryContainer,shape=androidx.compose.foundation.shape.RoundedCornerShape(10.dp)){Text("Подборки и избранное\nдоступны без регистрации.",Modifier.fillMaxWidth().padding(14.dp),style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.primary)}
        Group("Ваши данные"){UtilityRow("Избранное",Icons.Outlined.FavoriteBorder){model.tab(1);model.favorites()};UtilityRow("История поиска",Icons.Outlined.Search){model.push(CatalogPage.HISTORY);model.history()}}
        if(account.config?.capabilities?.contains("account_recovery")==true)Group("На другом устройстве"){
            Text("Сохраните код, чтобы открыть этот профиль на другом устройстве.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Primary("Создать код восстановления",!account.isGeneratingRecoveryCode&&!account.isDeleting){code=true}
            OutlinedButton(onClick=onRecover,modifier=Modifier.fillMaxWidth(),enabled=!account.isDeleting){Text("Восстановить по коду",style=MaterialTheme.typography.bodyMedium)}
            Text("Код даёт доступ к подборкам и избранному. Храните его в надёжном месте.",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
        }
        Group{UtilityRow("Помощь и документы",Icons.Outlined.Description){model.push(CatalogPage.HELP)}}
        Group("Управление данными"){TextButton(onClick={confirmingDelete=true},enabled=!account.isDeleting,colors=ButtonDefaults.textButtonColors(contentColor=MaterialTheme.colorScheme.error)){Text("Удалить мои данные")};Text("Диалоги, условия и избранное будут удалены с сервера и этого устройства.",style=MaterialTheme.typography.labelSmall);if(account.isDeleting)CircularProgressIndicator(Modifier.size(24.dp))}
    }
    if(code)RecoveryCodeDialog(session,account.isGeneratingRecoveryCode){code=false}
    if(confirmingDelete)AlertDialog(onDismissRequest={confirmingDelete=false},title={Text("Удалить все мои данные?")},text={Text("Все диалоги и избранные ЖК будут удалены. Восстановить удалённые сведения невозможно.")},confirmButton={TextButton(onClick={confirmingDelete=false;session.deleteAccount{failure->if(failure==null){model.clearScope();model.initialize()}else model.showError(failure)}}){Text("Удалить без восстановления")}},dismissButton={TextButton(onClick={confirmingDelete=false}){Text("Отмена")}})
}
@Composable internal fun Help(state:CatalogState,model:CatalogViewModel){
    val context=LocalContext.current;var expanded by rememberSaveable{mutableStateOf("source")}
    Column {
        Header(back=model::back)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),verticalArrangement=Arrangement.spacedBy(16.dp)){
            Heading("Помощь и документы");Text("Meken собирает сведения о ЖК из открытых источников.",style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Group("О данных"){
                Answer("Что означают источник и дата?","Источник — ссылка на публикацию. Дата сбора — когда Meken прочитал её.",expanded=="source"){expanded=if(expanded=="source")"" else "source"}
                if(expanded=="source")TextButton(onClick={model.push(CatalogPage.SOURCES);model.sources()},contentPadding=PaddingValues(0.dp)){Text("Источники каталога",style=MaterialTheme.typography.labelSmall)}
                HorizontalDivider()
                Answer("Почему в карточке нет сведений?","Источник может не публиковать отдельные поля. Отсутствие данных в Meken не означает отсутствие объекта или документа.",expanded=="missing"){expanded=if(expanded=="missing")"" else "missing"}
                HorizontalDivider()
                Answer("Можно ли забронировать квартиру?","Meken не бронирует квартиры и не передаёт заявки менеджерам. Откройте первоисточник, чтобы связаться с застройщиком.",expanded=="booking"){expanded=if(expanded=="booking")"" else "booking"}
            }
            Primary("Сообщить об ошибке в ЖК",state.config?.capabilities?.contains("data_reports")==true){model.push(CatalogPage.REPORT)}
            Group("Документы и данные"){
                safeHttpsUrl(state.config?.privacyUrl)?.let{url->UtilityRow("Политика конфиденциальности",Icons.Outlined.Description){openHttps(context,url)}} ?: Text("Политика конфиденциальности: ссылка не предоставлена",style=MaterialTheme.typography.labelSmall)
                HorizontalDivider();safeHttpsUrl(state.config?.termsUrl)?.let{url->UtilityRow("Условия использования",Icons.Outlined.Description){openHttps(context,url)}} ?: Text("Условия использования: ссылка не предоставлена",style=MaterialTheme.typography.labelSmall)
                HorizontalDivider();UtilityRow("Управление моими данными",Icons.Outlined.PersonOutline){model.tab(2)}
            }
            Group{
                Answer("Все ли ЖК есть в каталоге?","Каталог частичный. Публичные сведения, сроки и цены могут различаться между источниками.",expanded=="coverage"){expanded=if(expanded=="coverage")"" else "coverage"}
                Answer("Что означает цена ЖК «от»?","Это опубликованная стартовая цена проекта. Она не гарантирует бюджет конкретной квартиры. Минимум по наблюдавшимся лотам показывается отдельно.",expanded=="price"){expanded=if(expanded=="price")"" else "price"}
                Text("Подборки хранятся до ${state.config?.retentionDays ?: 90} дней с последней активности. Не отправляйте ИИН, документы и платёжные данные.",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
        }
    }
}
@Composable internal fun Sources(state:CatalogState,model:CatalogViewModel){val context=LocalContext.current;Column{
    Header("Источники каталога",model::back)
    LazyColumn(Modifier.fillMaxSize(),contentPadding=PaddingValues(16.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
        if(state.sources.isEmpty())item{Text("Нет сведений об источниках. Попробуйте обновить список.");TextButton(onClick=model::sources){Text("Обновить")}}
        items(state.sources,key={it.providerId}){s->Group(s.name){Text(if(s.provenance=="demo")"Демонстрационный источник" else s.cities.joinToString(", "),style=MaterialTheme.typography.bodySmall);val url=if(s.provenance=="demo")null else safeHttpsUrl(s.websiteUrl ?: s.sourceUrl);TextButton(onClick={openHttps(context,url)},enabled=url!=null){Text(if(url==null)"Ссылка недоступна" else "Открыть источник")}}}
    }
}}
@Composable internal fun Report(state:CatalogState,model:CatalogViewModel){
    var id by rememberSaveable{mutableStateOf(UUID.randomUUID().toString())};var project by rememberSaveable{mutableStateOf("")};var category by rememberSaveable{mutableStateOf("other")};var message by rememberSaveable{mutableStateOf("")};var sent by rememberSaveable{mutableStateOf(false)}
    val projects=(listOfNotNull(state.project)+state.results+state.favorites).distinctBy{it.id}
    Column {
        Header("Сообщить об ошибке",model::back)
        Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(16.dp),verticalArrangement=Arrangement.spacedBy(16.dp)){
            if(sent){Heading("Сообщение получено");Text("Это квитанция о получении, а не обещание ответа или исправления.",style=MaterialTheme.typography.bodyMedium)}
            else{
                Text("Выберите ЖК и опишите неточность. Не добавляйте личные данные.",style=MaterialTheme.typography.bodyMedium)
                ChoiceRow("Жилой комплекс",projects.firstOrNull{it.id==project}?.name ?: "Выберите ЖК",projects.map{it.id to it.name}){project=it}
                ChoiceRow("Что исправить",when(category){"price"->"Цена";"address"->"Адрес";"completion"->"Срок";"image"->"Изображение";"layout"->"Планировка";else->"Другое"},listOf("price" to "Цена","address" to "Адрес","completion" to "Срок","image" to "Изображение","layout" to "Планировка","other" to "Другое")){category=it}
                OutlinedTextField(message,{value->if(value.codePointCount(0,value.length)<=2000)message=value},Modifier.fillMaxWidth(),label={Text("Опишите неточность")},minLines=5,maxLines=12)
                Primary(if(state.busy)"Отправляем" else "Отправить сообщение",!state.busy&&project.isNotEmpty()&&message.isNotBlank()){model.report(id,project,category,message){sent=it}}
            }
        }
    }
}
@Composable private fun UtilityRow(title:String,icon:androidx.compose.ui.graphics.vector.ImageVector,onClick:()->Unit){TextButton(onClick=onClick,modifier=Modifier.fillMaxWidth(),contentPadding=PaddingValues(0.dp)){Icon(icon,null,Modifier.size(21.dp));Spacer(Modifier.width(10.dp));Text(title,Modifier.weight(1f),style=MaterialTheme.typography.bodyMedium,color=MaterialTheme.colorScheme.onSurface);Icon(Icons.Outlined.ChevronRight,null,Modifier.size(17.dp))}}
@Composable private fun Answer(title:String,text:String,expanded:Boolean,onClick:()->Unit){Column(verticalArrangement=Arrangement.spacedBy(8.dp)){TextButton(onClick=onClick,modifier=Modifier.fillMaxWidth(),contentPadding=PaddingValues(0.dp)){Text(title,Modifier.weight(1f),style=MaterialTheme.typography.labelMedium,color=MaterialTheme.colorScheme.onSurface);Icon(if(expanded)Icons.Outlined.ExpandLess else Icons.Outlined.ExpandMore,null,Modifier.size(17.dp))};if(expanded)Text(text,style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)}}
