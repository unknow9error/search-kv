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
import androidx.compose.ui.layout.ContentScale
import coil.compose.SubcomposeAsyncImage
import kotlinx.serialization.json.*
import kz.unknown.meken.R
import kz.unknown.meken.core.safeHttpsUrl
import kz.unknown.meken.core.catalog.*
import kz.unknown.meken.ui.openHttps

@Composable internal fun ProjectScreen(state:CatalogState,model:CatalogViewModel){state.project?.let{p->
    var section by rememberSaveable(p.id){mutableStateOf("Обзор")};val context=LocalContext.current
    Column {
        Header(p.name,model::back){IconButton(onClick={model.favorite(p)}){Icon(if(state.favorites.any{it.id==p.id})Icons.Outlined.Favorite else Icons.Outlined.FavoriteBorder,"Сохранить ЖК",Modifier.size(20.dp))};TextButton(onClick={model.select(p.id);if(state.selection.size>=1&&p.id !in state.selection)model.compare()}){Text("Сравнить",style=MaterialTheme.typography.labelSmall)}}
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(14.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
            Artwork(p,Modifier.fillMaxWidth().height(225.dp));Heading(p.name)
            Text(listOfNotNull(p.developerName,p.city,p.district.ifEmpty{null}).joinToString(" · "),style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Group{Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(5.dp)){Text(if(p.displayPrice.kind=="observed_listing_minimum")"Минимум по опубликованным лотам" else "Цена ЖК «от»",style=MaterialTheme.typography.labelSmall);Text(p.displayPrice.label,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Bold)}
                Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(5.dp)){Text(p.buildings.firstOrNull()?.name ?: "Корпус не опубликован",style=MaterialTheme.typography.labelMedium);Text(p.stageLabel,style=MaterialTheme.typography.labelSmall);Text("Заявленный срок",style=MaterialTheme.typography.labelSmall);Text(p.completion ?: "Не опубликован",style=MaterialTheme.typography.labelSmall)}
            }}
            Text(p.sourceLabel,style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Row{listOf("Обзор","Планировки","Документы").forEach{name->TextButton(onClick={section=name},modifier=Modifier.weight(1f),colors=ButtonDefaults.textButtonColors(containerColor=if(section==name)MaterialTheme.colorScheme.primary else MaterialTheme.colorScheme.surfaceVariant,contentColor=if(section==name)MaterialTheme.colorScheme.onPrimary else MaterialTheme.colorScheme.onSurface)){Text(name,style=MaterialTheme.typography.labelSmall)}}}
            when(section){
                "Обзор"->{p.address?.let{Text(it,style=MaterialTheme.typography.bodyMedium)};Text("Отделка: ${p.finish ?: "Не опубликована"}",style=MaterialTheme.typography.bodyMedium);p.buildings.forEach{Text("${it.name} · ${it.completion ?: "Срок не опубликован"}",style=MaterialTheme.typography.bodySmall)};Text("Сведения относятся к публичным публикациям. Цена «от» и опубликованные лоты не подтверждают наличие квартиры для покупки.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant);TextButton(onClick={model.push(CatalogPage.CONVERSATION)}){Text("Уточнить в разговоре")}}
                "Планировки"->{if(p.layouts.isEmpty())Text("Нет данных о опубликованных планировках.",style=MaterialTheme.typography.bodyMedium);p.layouts.forEach{layout->FieldRow("Тип планировки",layout.parameterLabel,Icons.Outlined.GridView){model.layout(layout)}}}
                else->{if(p.documents.isEmpty())Text("Нет данных о документах. Это не означает отсутствие разрешений у застройщика.",style=MaterialTheme.typography.bodyMedium);p.documents.forEach{d->val url=if(p.isDemo)null else safeHttpsUrl(d.url);TextButton(onClick={openHttps(context,url)},enabled=url!=null){Text(d.name)}}}
            }
        }
        Box(Modifier.padding(14.dp)){Primary("Открыть первоисточник",p.sourceLink!=null){openHttps(context,p.sourceLink)}}
        if(p.isDemo)Text("Первоисточник недоступен в демо-каталоге",style=MaterialTheme.typography.labelSmall,modifier=Modifier.padding(horizontal=14.dp))
    }
}}
@Composable internal fun LayoutScreen(state:CatalogState,model:CatalogViewModel){state.layout?.let{layout->val context=LocalContext.current
    Column {
        Header("Планировка",model::back){state.project?.let{p->IconButton(onClick={model.favorite(p)}){Icon(Icons.Outlined.FavoriteBorder,"Сохранить ЖК")}}}
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(18.dp),verticalArrangement=Arrangement.spacedBy(16.dp)){
            Text(state.project?.name ?: layout.name,style=MaterialTheme.typography.titleSmall);Text(state.project?.buildings?.firstOrNull()?.name ?: "Тип планировки",style=MaterialTheme.typography.labelSmall)
            if(layout.provenance=="demo"&&layout.externalId.startsWith("design-"))ReferenceRegion(R.drawable.catalogdemolayout,90,432,689,712,Modifier.fillMaxWidth().height(340.dp))
            else safeHttpsUrl(layout.imageUrl)?.let{url->SubcomposeAsyncImage(url,"Планировка",Modifier.fillMaxWidth().height(340.dp),contentScale=ContentScale.Fit,error={Text("Не удалось загрузить изображение")},loading={CircularProgressIndicator()})} ?: Text("Изображение не опубликовано",style=MaterialTheme.typography.bodyMedium)
            Text("Тип планировки",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant);Text(layout.parameterLabel,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Bold)
            Group{Text("Цена не опубликована",style=MaterialTheme.typography.bodyMedium)}
            Text("Это тип планировки. Цена, этаж и наличие конкретной квартиры не подтверждаются.",style=MaterialTheme.typography.bodySmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            Text(state.project?.providerName ?: "Источник",style=MaterialTheme.typography.bodySmall)
            Row(verticalAlignment=Alignment.CenterVertically){Text("Собрано ${dateLabel(layout.observedAt)}",Modifier.weight(1f),style=MaterialTheme.typography.labelSmall);TextButton(onClick=model::refreshLayout){Text("Обновить сведения",style=MaterialTheme.typography.labelSmall)}}
        }
        Box(Modifier.padding(18.dp)){Primary("Открыть первоисточник",layout.provenance!="demo"&&safeHttpsUrl(layout.sourceUrl)!=null){openHttps(context,layout.sourceUrl)}}
        if(layout.provenance=="demo")Text("Демонстрационная планировка",style=MaterialTheme.typography.labelSmall,modifier=Modifier.padding(horizontal=18.dp))
    }
}}
@Composable internal fun Comparison(state:CatalogState,model:CatalogViewModel){Column{
    Header("Сравнение ЖК",model::back){Text("${state.comparison?.projects?.size ?: 0} ЖК",style=MaterialTheme.typography.labelSmall)}
    state.comparison?.let{compare->Column(Modifier.weight(1f).verticalScroll(rememberScrollState()).horizontalScroll(rememberScrollState()).padding(16.dp),verticalArrangement=Arrangement.spacedBy(14.dp)){
        Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){compare.projects.forEach{p->Column(Modifier.width(165.dp),verticalArrangement=Arrangement.spacedBy(9.dp)){Artwork(p,Modifier.fillMaxWidth().height(125.dp));Text(p.name,style=MaterialTheme.typography.titleSmall,fontWeight=FontWeight.Bold);Text(p.developerName ?: "Застройщик не опубликован",style=MaterialTheme.typography.labelSmall);Text(p.sourceLabel,style=MaterialTheme.typography.labelSmall);Primary("Открыть ЖК"){model.project(p)}}}}
        CompareGroup("Цена ЖК «от»",compare.projects){it.publishedStartingPrice?.label ?: "Цена не опубликована"}
        CompareGroup("Корпус и срок сдачи",compare.projects){(it.buildings.firstOrNull()?.name ?: "Корпус не опубликован")+"\n"+(it.buildings.firstOrNull()?.completion ?: it.completion ?: "Срок не опубликован")}
        CompareGroup("Отделка",compare.projects){it.finish ?: "Не опубликована"}
        var details by rememberSaveable{mutableStateOf(false)}
        TextButton(onClick={details=!details}){Text("Дополнительные сведения",style=MaterialTheme.typography.labelSmall)}
        if(details)compare.rows.filter{it.key !in listOf("published_starting_price","completion","finish")}.forEach{row->CompareGroup(row.label,compare.projects){p->val value=row.values.firstOrNull{it.projectId==p.id}?.value;val amount=if(row.key.contains("price"))(value as?JsonPrimitive)?.longOrNull else null;if(row.key=="stage")p.stageLabel else amount?.let{"Лоты от ${millions(it)} млн ₸"} ?: (value as?JsonPrimitive)?.contentOrNull ?: "Не опубликовано"}}
    };if(compare.projects.size==3)Text("Прокрутите вправо, чтобы посмотреть третий ЖК",style=MaterialTheme.typography.labelSmall,modifier=Modifier.padding(10.dp))}
}}
@Composable internal fun Favorites(state:CatalogState,model:CatalogViewModel){var choosing by rememberSaveable{mutableStateOf(false)};Column{
    Row(Modifier.padding(16.dp),verticalAlignment=Alignment.CenterVertically){Heading("Избранное");Spacer(Modifier.weight(1f));TextButton(onClick={choosing=!choosing}){Text(if(choosing)"Отмена" else "Выбрать",style=MaterialTheme.typography.labelSmall)}}
    LazyColumn(Modifier.weight(1f).fillMaxWidth(),contentPadding=PaddingValues(horizontal=14.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
        item{Text(if(choosing)"Выберите до 3 ЖК." else "Сохранённые жилые комплексы",style=MaterialTheme.typography.labelSmall)}
        if(state.favorites.isEmpty())item{Text("Пока нет сохранённых ЖК. Нажмите на сердечко в карточке.",style=MaterialTheme.typography.bodyMedium)}
        items(state.favorites,key={it.id}){ProjectCard(it,state,model,choosing=choosing)}
    }
    if(choosing)Box(Modifier.padding(14.dp)){Primary("Сравнить ${state.selection.size} ЖК",state.selection.size>=2&&!state.busy,model::compare)}
}}

@Composable private fun CompareGroup(title:String,projects:List<CatalogProject>,value:(CatalogProject)->String){Group(title){Row(horizontalArrangement=Arrangement.spacedBy(10.dp)){projects.forEach{Text(value(it),Modifier.width(165.dp),style=MaterialTheme.typography.bodySmall)}}}}
