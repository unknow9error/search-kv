package kz.unknown.meken.ui.catalog

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.*
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.unit.dp
import kz.unknown.meken.core.catalog.*
import kotlin.math.roundToLong

@Composable internal fun Filters(state:CatalogState,model:CatalogViewModel){
    var draft by remember{mutableStateOf(state.criteria)}
    var price by rememberSaveable{mutableStateOf(state.criteria.priceMax?.let{(it/1_000_000.0).toString()}.orEmpty())}
    var deadline by rememberSaveable{mutableStateOf(state.criteria.completionBefore.orEmpty())}
    var area by rememberSaveable{mutableStateOf(state.criteria.areaMin?.toString().orEmpty())}
    var floorMin by rememberSaveable{mutableStateOf(state.criteria.floorMin?.toString().orEmpty())};var floorMax by rememberSaveable{mutableStateOf(state.criteria.floorMax?.toString().orEmpty())}
    var stages by rememberSaveable{mutableStateOf(false)};var amenities by rememberSaveable{mutableStateOf(false)};var parameters by rememberSaveable{mutableStateOf(false)};var error by rememberSaveable{mutableStateOf<String?>(null)}
    Column {
        Header{TextButton(onClick=model::back){Text("Закрыть",style=MaterialTheme.typography.labelSmall)}}
        Row(Modifier.padding(horizontal=18.dp,vertical=12.dp),verticalAlignment=Alignment.CenterVertically){Heading("Фильтры");Spacer(Modifier.weight(1f));TextButton(onClick={draft=ProjectCriteria(city=draft.city);price="";area="";floorMin="";floorMax="";deadline="";error=null}){Text("Сбросить",style=MaterialTheme.typography.labelSmall)}}
        Column(Modifier.weight(1f).fillMaxWidth().verticalScroll(rememberScrollState()).padding(horizontal=18.dp),verticalArrangement=Arrangement.spacedBy(12.dp)){
            ChoiceRow("Город",draft.city ?: "Выберите город",state.config?.cities.orEmpty().map{it to it}){city->draft=draft.copy(city=city,districts=emptyList());model.facets(city)}
            ChoiceRow("Район",draft.districts.joinToString(", ").ifEmpty{"Все районы"},listOf("" to "Все районы")+(if(state.facetCity==draft.city)state.facets?.districts else null).orEmpty().map{it.value to it.value}){draft=draft.copy(districts=if(it.isEmpty())emptyList()else listOf(it))}
            ChoiceRow("Застройщик",draft.developerNames.firstOrNull() ?: "Любой",listOf("" to "Любой")+(if(state.facetCity==draft.city)state.facets?.developerNames else null).orEmpty().map{it.value to it.value}){draft=draft.copy(developerNames=if(it.isEmpty())emptyList()else listOf(it))}
            Group {
                ChoiceRow("Тип цены",draft.priceModeLabel,listOf("published_starting_price" to "Цена ЖК «от»","observed_listing_minimum" to "Минимум по опубликованным лотам")){draft=draft.copy(priceMode=it)}
                NumberField("До, млн ₸",price){price=it}
                Text("Цена «от» не подтверждает цену конкретной квартиры.",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
            }
            FieldRow("Сроки и стадия",if(draft.stages.isEmpty()&&deadline.isEmpty())"Любые" else "Выбраны"){stages=!stages}
            if(stages)Group{
                ChoiceRow("Стадия",when(draft.stages.firstOrNull()){ "commissioned"->"Сдан";"under_construction"->"Строится";else->"Любая"},listOf("" to "Любая","under_construction" to "Строится","commissioned" to "Сдан")){draft=draft.copy(stages=if(it.isEmpty())emptyList()else listOf(it))}
                OutlinedTextField(deadline,{deadline=it},Modifier.fillMaxWidth(),label={Text("Срок до, ГГГГ-ММ-ДД")},singleLine=true)
            }
            FieldRow("Инфраструктура",if(draft.requiredAmenities.isEmpty())"Не выбрана" else "${draft.requiredAmenities.size} условия"){amenities=!amenities}
            if(amenities)Group{
                listOf("school" to "Школа","kindergarten" to "Детский сад","park" to "Парк","transit" to "Остановка").forEach{(kind,title)->Row(verticalAlignment=Alignment.CenterVertically){Text(title,Modifier.weight(1f),style=MaterialTheme.typography.bodyMedium);Switch(kind in draft.requiredAmenities,{enabled->draft=draft.copy(requiredAmenities=if(enabled)(draft.requiredAmenities+kind).distinct()else draft.requiredAmenities-kind)})}}
                ChoiceRow("Область",when(draft.amenityScope){"complex"->"В ЖК";"bigville"->"В бигвилле";else->"Рядом"},listOf("nearby" to "Рядом","complex" to "В ЖК","bigville" to "В бигвилле")){draft=draft.copy(amenityScope=it)}
                if(draft.amenityScope=="nearby")ChoiceRow("Радиус","${draft.amenityRadiusM} м",listOf("500" to "500 м","1000" to "1 км","2000" to "2 км")){draft=draft.copy(amenityRadiusM=it.toInt())}
                Text("Обязательные условия требуют опубликованных данных о действующих объектах. Расстояния по прямой.",style=MaterialTheme.typography.labelSmall)
            }
            FieldRow("Параметры квартир","Комнаты, площадь, этаж"){parameters=!parameters}
            if(parameters)Group{
                Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(5.dp)){(1..8).forEach{room->Chip("$room",room in draft.rooms){draft=draft.copy(rooms=if(room in draft.rooms)draft.rooms-room else draft.rooms+room)}}}
                NumberField("Площадь от, м²",area){area=it};NumberField("Этаж от",floorMin){floorMin=it};NumberField("Этаж до",floorMax){floorMax=it}
                Text("Параметры должны совпасть у одного опубликованного лота. Цена ЖК «от» не является его бюджетом.",style=MaterialTheme.typography.labelSmall)
            }
            error?.let{Text(it,color=MaterialTheme.colorScheme.error,style=MaterialTheme.typography.bodySmall)}
            Spacer(Modifier.height(15.dp))
        }
        Box(Modifier.padding(18.dp)){Primary(if(state.busy)"Ищем ЖК" else "Показать ЖК",!state.busy){
            val money=price.replace(',','.').toDoubleOrNull();val size=area.replace(',','.').toDoubleOrNull();val low=floorMin.toIntOrNull();val high=floorMax.toIntOrNull()
            error=when{
                price.isNotBlank()&&(money==null||!money.isFinite()||money<=0||money>10000)->"Введите сумму больше нуля и не больше 10 000 млн ₸."
                area.isNotBlank()&&(size==null||!size.isFinite()||size !in 1.0..2000.0)->"Проверьте площадь."
                floorMin.isNotBlank()&&(low==null||low !in 1..150)->"Этаж должен быть от 1 до 150."
                floorMax.isNotBlank()&&(high==null||high !in 1..150)->"Этаж должен быть от 1 до 150."
                low!=null&&high!=null&&low>high->"Этаж от не может быть выше этажа до."
                deadline.isNotBlank()&&runCatching{java.time.LocalDate.parse(deadline)}.isFailure->"Введите дату в формате ГГГГ-ММ-ДД."
                else->null
            }
            if(error==null){model.criteria(draft.copy(priceMax=money?.times(1_000_000)?.roundToLong(),completionBefore=deadline.ifBlank{null},areaMin=size,floorMin=low,floorMax=high));model.search()}
        }}
    }
}
@Composable private fun NumberField(title:String,value:String,onChange:(String)->Unit){OutlinedTextField(value,{if(it.length<=20)onChange(it)},Modifier.fillMaxWidth(),label={Text(title,style=MaterialTheme.typography.bodySmall)},singleLine=true,keyboardOptions=KeyboardOptions(keyboardType=KeyboardType.Decimal))}
