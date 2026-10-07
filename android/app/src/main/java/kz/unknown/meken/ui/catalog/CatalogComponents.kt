package kz.unknown.meken.ui.catalog

import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.automirrored.outlined.ArrowBack
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.ui.*
import androidx.compose.ui.graphics.*
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.res.imageResource
import androidx.compose.ui.unit.*
import androidx.compose.ui.text.font.FontWeight
import coil.compose.SubcomposeAsyncImage
import kz.unknown.meken.R
import kz.unknown.meken.core.safeHttpsUrl
import kz.unknown.meken.core.catalog.*

@Composable internal fun Header(title:String="",back:(()->Unit)?=null,actions:@Composable RowScope.()->Unit={}) {
    Row(Modifier.fillMaxWidth().heightIn(min=48.dp).padding(horizontal=14.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(8.dp)) {
        back?.let { IconButton(onClick=it,modifier=Modifier.size(36.dp)){Icon(Icons.AutoMirrored.Outlined.ArrowBack,"Назад",Modifier.size(20.dp))} }
        Text(if(title.isEmpty())"meken" else title,Modifier.weight(1f),style=if(title.isEmpty())MaterialTheme.typography.titleLarge else MaterialTheme.typography.titleSmall,fontWeight=FontWeight.Bold,maxLines=1)
        actions()
    }
}
@Composable internal fun Heading(text:String) { Text(text,style=MaterialTheme.typography.headlineSmall,fontWeight=FontWeight.Bold) }
@Composable internal fun FieldRow(title:String,value:String,icon:androidx.compose.ui.graphics.vector.ImageVector?=null,onClick:()->Unit) {
    Surface(onClick=onClick,modifier=Modifier.fillMaxWidth(),color=MaterialTheme.colorScheme.surfaceVariant,contentColor=MaterialTheme.colorScheme.onSurface,shape=RoundedCornerShape(9.dp)) {
        Row(Modifier.heightIn(min=48.dp).padding(12.dp),verticalAlignment=Alignment.CenterVertically,horizontalArrangement=Arrangement.spacedBy(10.dp)) {
            icon?.let { Icon(it,null,Modifier.size(20.dp),tint=MaterialTheme.colorScheme.onSurfaceVariant) }
            Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(3.dp)) {
                if(title.isNotEmpty()) Text(title,style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                Text(value,style=MaterialTheme.typography.bodyMedium)
            }
            Icon(Icons.Outlined.ExpandMore,null,Modifier.size(18.dp),tint=MaterialTheme.colorScheme.onSurfaceVariant)
        }
    }
}
@Composable internal fun Primary(text:String,enabled:Boolean=true,onClick:()->Unit) { Button(onClick=onClick,enabled=enabled,modifier=Modifier.fillMaxWidth().heightIn(min=48.dp),shape=RoundedCornerShape(8.dp),contentPadding=PaddingValues(12.dp)) { Text(text,style=MaterialTheme.typography.bodyMedium,fontWeight=FontWeight.SemiBold) } }
@Composable internal fun ChoiceRow(title: String, value: String, options: List<Pair<String, String>>, onSelect: (String) -> Unit) {
    var expanded by remember { mutableStateOf(false) }
    Box {
        FieldRow(title, value, onClick = { expanded = true })
        DropdownMenu(expanded = expanded, onDismissRequest = { expanded = false }) {
            options.forEach { (key, label) ->
                DropdownMenuItem(text = { Text(label) }, onClick = { expanded = false; onSelect(key) })
            }
        }
    }
}
@Composable internal fun Group(title:String?=null,content:@Composable ColumnScope.()->Unit) {
    Surface(color=MaterialTheme.colorScheme.surfaceVariant,contentColor=MaterialTheme.colorScheme.onSurface,shape=RoundedCornerShape(10.dp)) {
        Column(Modifier.fillMaxWidth().padding(14.dp),verticalArrangement=Arrangement.spacedBy(12.dp)) { title?.let{Text(it,style=MaterialTheme.typography.titleSmall,fontWeight=FontWeight.SemiBold)};content() }
    }
}
@Composable internal fun Chip(text:String,selected:Boolean=false,onClick:()->Unit) { FilterChip(selected,onClick,label={Text(text,style=MaterialTheme.typography.labelSmall)},shape=RoundedCornerShape(20.dp)) }
@Composable internal fun ReferenceRegion(asset:Int,x:Int,y:Int,width:Int,height:Int,modifier:Modifier=Modifier) {
    val bitmap=ImageBitmap.imageResource(asset)
    Canvas(modifier){ drawImage(bitmap,srcOffset=IntOffset(x,y),srcSize=IntSize(width,height),dstSize=IntSize(size.width.toInt(),size.height.toInt())) }
}
@Composable internal fun Artwork(project:CatalogProject,modifier:Modifier=Modifier) {
    Box(modifier.background(MaterialTheme.colorScheme.surfaceVariant),contentAlignment=Alignment.Center) {
        if(project.isDemo&&project.externalId.startsWith("design-")) ReferenceRegion(R.drawable.catalogdemoprojects,34,if(project.externalId=="design-river")1154 else 445,783,if(project.externalId=="design-river")359 else 395,Modifier.fillMaxSize())
        else { val image=project.images.firstOrNull{it.kind=="facade"||it.kind=="site"}?.url?.let(::safeHttpsUrl)
            if(image!=null)SubcomposeAsyncImage(image,"Изображение ЖК",Modifier.fillMaxSize(),contentScale=ContentScale.Crop,loading={CircularProgressIndicator(Modifier.size(24.dp))},error={MissingImage()})
            else MissingImage()
        }
    }
}
@Composable private fun MissingImage(){Column(horizontalAlignment=Alignment.CenterHorizontally,verticalArrangement=Arrangement.spacedBy(7.dp)){Icon(Icons.Outlined.Apartment,null,Modifier.size(30.dp));Text("Изображение не опубликовано",style=MaterialTheme.typography.labelSmall)}}
@Composable internal fun ProjectCard(project:CatalogProject,state:CatalogState,model:CatalogViewModel,compact:Boolean=false,choosing:Boolean=false){
    Surface(color=MaterialTheme.colorScheme.surface,shape=RoundedCornerShape(10.dp),border=BorderStroke(1.dp,MaterialTheme.colorScheme.outlineVariant)) {
        Column {
            Box {
                Surface(onClick={model.project(project)}){Artwork(project,Modifier.fillMaxWidth().height(if(compact)110.dp else 180.dp))}
                IconButton(onClick={if(choosing)model.select(project.id)else model.favorite(project)},enabled=project.id !in state.favoriteBusy,
                    modifier=Modifier.align(Alignment.TopEnd).padding(8.dp).size(36.dp).background(Color.White,RoundedCornerShape(30.dp))){
                    Icon(if(choosing){if(project.id in state.selection)Icons.Outlined.CheckCircle else Icons.Outlined.RadioButtonUnchecked}else if(state.favorites.any{it.id==project.id})Icons.Outlined.Favorite else Icons.Outlined.FavoriteBorder,
                        if(choosing)"Выбрать ${project.name}" else "Сохранить ${project.name}",Modifier.size(20.dp),tint=MaterialTheme.colorScheme.primary)
                }
            }
            Column(Modifier.padding(12.dp),verticalArrangement=Arrangement.spacedBy(5.dp)){
                TextButton(onClick={model.project(project)},contentPadding=PaddingValues(0.dp),modifier=Modifier.heightIn(min=24.dp)){Text(project.name,style=MaterialTheme.typography.titleSmall,fontWeight=FontWeight.Bold,color=MaterialTheme.colorScheme.onSurface)}
                Text("${project.developerName ?: "Застройщик не опубликован"} · ${project.district.ifEmpty{project.city}}",style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                Row(verticalAlignment=Alignment.CenterVertically){Column(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(5.dp)){
                    Text(project.displayPrice.label,style=MaterialTheme.typography.titleMedium,fontWeight=FontWeight.Bold)
                    if(!compact)Text(listOfNotNull(project.buildings.firstOrNull()?.name,project.completion).joinToString(" · "),style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant)
                };if(!compact&&!choosing)OutlinedButton(onClick={model.select(project.id)},enabled=project.id in state.selection||state.selection.size<3,shape=RoundedCornerShape(6.dp),contentPadding=PaddingValues(8.dp)){Icon(Icons.Outlined.BarChart,null,Modifier.size(16.dp));Text(if(project.id in state.selection)"В сравнении" else "Сравнить",style=MaterialTheme.typography.labelSmall)}}
                Text(project.sourceLabel,style=MaterialTheme.typography.labelSmall,color=MaterialTheme.colorScheme.onSurfaceVariant,modifier=Modifier.padding(top=5.dp))
            }
        }
    }
}
@Composable internal fun CriteriaChips(state:CatalogState,model:CatalogViewModel){Row(Modifier.horizontalScroll(rememberScrollState()),horizontalArrangement=Arrangement.spacedBy(8.dp)){
    state.criteria.districts.forEach{d->Chip("$d ×"){model.criteria(state.criteria.copy(districts=state.criteria.districts-d));model.search()}}
    if(state.criteria.priceMax!=null)Chip("${state.criteria.priceModeLabel} · ${state.criteria.priceSummary} ×"){model.criteria(state.criteria.copy(priceMax=null));model.search()}
}}
